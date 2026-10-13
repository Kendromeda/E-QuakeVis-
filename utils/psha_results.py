"""Read PSHA results produced by ``scripts/run_psha.py`` (OpenQuake exports).

This module only validates and displays finished runs in
``data/psha/results/<run_id>/`` (launching runs lives in ``utils/psha_jobs.py``). Missing or inconsistent files raise
``PSHAResultError``; no hazard values are interpolated or synthesized.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088
MANIFEST_KEYS = (
    "run_id", "openquake_version", "model", "region", "num_sites", "site_condition",
    "investigation_time_years", "poes", "imts", "outputs",
)
IMT_LABELS = {
    "PGA": "PGA (percepatan tanah puncak)",
    "SA(0.2)": "SA 0,2 detik (spektral periode pendek)",
    "SA(1.0)": "SA 1,0 detik (spektral periode 1 detik)",
}
# Fixed class limits (g) so maps of different runs and regions are comparable.
HAZARD_BREAKS_G = {
    "PGA": (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0),
    "SA(0.2)": (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0, 1.25, 1.5, 2.0),
    "SA(1.0)": (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.8, 1.0),
}
HAZARD_COLORS = (
    "#fff7bc", "#fee391", "#fec44f", "#fe9929", "#ec7014", "#cc4c02",
    "#d7301f", "#b30000", "#7f0000", "#7a0177", "#49006a", "#25003a",
)


# Scientific basis of each GEM IDN model version, for captions and map credits.
MODEL_BASES = {
    "v2017": "PuSGeN 2017 (Irsyam dkk.)",
    "v2024": "PuSGeN 2024",
}


def vs30_label(site_condition: dict[str, Any]) -> str:
    """"Vs30 760 m/s" or "Vs30 USGS per situs (180-900, median 412 m/s)"."""
    if site_condition.get("vs30_source") == "usgs":
        return (f"Vs30 USGS per situs ({site_condition['vs30_min_m_s']:g}–"
                f"{site_condition['vs30_max_m_s']:g}, median "
                f"{site_condition['vs30_m_s']:g} m/s)")
    return f"Vs30 {site_condition['vs30_m_s']:g} m/s"


def model_label(manifest: dict[str, Any]) -> str:
    """For example "PuSGeN 2024 via GEM IDN v2024.0.0"."""
    if manifest.get("source_model"):
        return (f"Model sumber sendiri ({manifest['source_model']['name']}) + sesar/megathrust "
                "PuSGeN 2024")
    version = str(manifest["model"]["model_version"])
    basis = next((label for prefix, label in MODEL_BASES.items()
                  if version.startswith(prefix)), "Model")
    return f"{basis} via GEM IDN {version}"


class PSHAResultError(ValueError):
    """A PSHA result folder is missing, incomplete or inconsistent."""


@dataclass(frozen=True)
class HazardColumn:
    imt: str
    poe: float
    return_period_years: int
    column: str


@dataclass(frozen=True)
class PSHARun:
    run_id: str
    path: Path
    manifest: dict[str, Any]

    @property
    def spacing(self) -> float:
        return float(self.manifest["region"]["spacing_degrees"])

    @property
    def columns(self) -> list[HazardColumn]:
        return hazard_columns(self.manifest)


def return_period(poe: float, investigation_time: float) -> float:
    """Poissonian return period of a probability of exceedance."""
    if not 0 < poe < 1 or investigation_time <= 0:
        raise PSHAResultError("Probabilitas terlampaui atau waktu investigasi tidak valid.")
    return -investigation_time / math.log1p(-poe)


def hazard_columns(manifest: dict[str, Any]) -> list[HazardColumn]:
    """Hazard-map columns ``<IMT>-<poe>`` in IMT then return-period order."""
    time_span = float(manifest["investigation_time_years"])
    return [
        HazardColumn(imt, float(poe), round(return_period(float(poe), time_span)), f"{imt}-{poe}")
        for imt in manifest["imts"] for poe in manifest["poes"]
    ]


def _read_manifest(run_dir: Path) -> dict[str, Any]:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PSHAResultError(f"Manifest PSHA {run_dir.name} tidak dapat dibaca.") from exc
    missing = [key for key in MANIFEST_KEYS if key not in manifest]
    if missing:
        raise PSHAResultError(f"Manifest PSHA {run_dir.name} tidak lengkap: {', '.join(missing)}.")
    if "hazard_map.csv" not in manifest["outputs"]:
        raise PSHAResultError(f"Run PSHA {run_dir.name} tidak memiliki peta hazard.")
    return manifest


def list_psha_runs(results_dir: str | Path) -> list[PSHARun]:
    """Finished runs (folders with a manifest), largest site set first."""
    root = Path(results_dir)
    if not root.is_dir():
        return []
    runs = [
        PSHARun(path.name, path, _read_manifest(path))
        for path in sorted(root.iterdir())
        if path.is_dir() and (path / "manifest.json").is_file()
    ]
    return sorted(runs, key=lambda run: (-int(run.manifest["num_sites"]), run.run_id))


def _file_key(path: Path) -> tuple[str, int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise PSHAResultError(f"Berkas hasil PSHA tidak ditemukan: {path.name}") from exc
    return str(path), stat.st_mtime_ns, stat.st_size


@lru_cache(maxsize=8)
def _read_csv(path: str, modified_ns: int, size: int) -> pd.DataFrame:
    del modified_ns, size  # Cache key only: replaced files are re-read.
    try:
        return pd.read_csv(path)
    except (OSError, ValueError) as exc:
        raise PSHAResultError(f"Berkas hasil PSHA tidak dapat dibaca: {Path(path).name}") from exc


def _validate_sites(frame: pd.DataFrame, run: PSHARun, name: str) -> None:
    if list(frame.columns[:2]) != ["lon", "lat"]:
        raise PSHAResultError(f"{name} harus diawali kolom lon dan lat.")
    if len(frame) != int(run.manifest["num_sites"]):
        raise PSHAResultError(
            f"{name} berisi {len(frame)} situs, manifest mencatat {run.manifest['num_sites']}.")
    lon, lat = frame["lon"].to_numpy(float), frame["lat"].to_numpy(float)
    if not (np.isfinite(lon).all() and np.isfinite(lat).all()):
        raise PSHAResultError(f"Koordinat situs pada {name} tidak valid.")
    if (np.abs(lat) > 90).any() or (np.abs(lon) > 180).any():
        raise PSHAResultError(f"Koordinat situs pada {name} di luar batas geografis.")


def load_hazard_map(run: PSHARun) -> pd.DataFrame:
    """lon, lat and one column (g) per IMT/return period, as exported by OpenQuake."""
    frame = _read_csv(*_file_key(run.path / "hazard_map.csv")).copy()
    _validate_sites(frame, run, "hazard_map.csv")
    for column in run.columns:
        if column.column not in frame.columns:
            raise PSHAResultError(f"Kolom {column.column} tidak ada pada hazard_map.csv.")
        values = pd.to_numeric(frame[column.column], errors="coerce").to_numpy(float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise PSHAResultError(f"Nilai {column.column} harus angka tidak negatif.")
    return frame


def load_hazard_curves(run: PSHARun, imt: str) -> tuple[np.ndarray, pd.DataFrame]:
    """Intensity levels (g) and per-site probabilities of exceedance for one IMT."""
    if imt not in run.manifest["imts"]:
        raise PSHAResultError(f"IMT {imt} tidak dihitung pada run {run.run_id}.")
    name = f"hazard_curves-{imt}.csv"
    frame = _read_csv(*_file_key(run.path / name)).copy()
    _validate_sites(frame, run, name)
    poe_columns = [column for column in frame.columns if column.startswith("poe-")]
    if not poe_columns:
        raise PSHAResultError(f"{name} tidak memiliki kolom poe-<level>.")
    levels = np.array([float(column[4:]) for column in poe_columns])
    poes = frame[poe_columns].to_numpy(float)
    if (np.diff(levels) <= 0).any():
        raise PSHAResultError(f"Level intensitas pada {name} harus naik.")
    if not np.isfinite(poes).all() or (poes < 0).any() or (poes > 1).any():
        raise PSHAResultError(f"Probabilitas pada {name} harus berada di antara 0 dan 1.")
    return levels, frame


def nearest_site(frame: pd.DataFrame, lon: float, lat: float) -> tuple[int, float]:
    """Row position of the closest site and its great-circle distance (km)."""
    lon1, lat1 = np.radians(frame["lon"].to_numpy(float)), np.radians(frame["lat"].to_numpy(float))
    lon2, lat2 = math.radians(lon), math.radians(lat)
    a = (np.sin((lat1 - lat2) / 2) ** 2
         + np.cos(lat1) * math.cos(lat2) * np.sin((lon1 - lon2) / 2) ** 2)
    distances = 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    index = int(np.argmin(distances))
    return index, float(distances[index])


def hazard_grid(
    frame: pd.DataFrame, column: str, spacing: float,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    """Regular grid of site values, north row first; cells without a site are NaN.

    Returns ``(values, (west, south, east, north))`` where the bounds are the
    outer edges of the cells centred on the sites.
    """
    cols = np.rint(frame["lon"].to_numpy(float) / spacing).astype(int)
    rows = np.rint(frame["lat"].to_numpy(float) / spacing).astype(int)
    if (np.abs(cols * spacing - frame["lon"].to_numpy(float)) > spacing * 1e-3).any():
        raise PSHAResultError("Situs PSHA tidak berada pada grid beraturan sesuai spasi manifest.")
    values = np.full((rows.max() - rows.min() + 1, cols.max() - cols.min() + 1), np.nan)
    values[rows.max() - rows, cols - cols.min()] = frame[column].to_numpy(float)
    half = spacing / 2
    bounds = (cols.min() * spacing - half, rows.min() * spacing - half,
              cols.max() * spacing + half, rows.max() * spacing + half)
    return values, bounds


def hazard_classes(values: np.ndarray, breaks: tuple[float, ...]) -> np.ndarray:
    """Class index per value (``breaks[i] <= v < breaks[i+1]``); NaN gives -1."""
    classes = np.searchsorted(np.asarray(breaks[1:]), values, side="right")
    return np.where(np.isfinite(values), classes, -1)


def hazard_legend_labels(breaks: tuple[float, ...]) -> list[str]:
    def fmt(value: float) -> str:
        return f"{value:g}".replace(".", ",")
    labels = [f"{fmt(low)} – {fmt(high)}" for low, high in zip(breaks, breaks[1:])]
    return labels + [f"≥ {fmt(breaks[-1])}"]
