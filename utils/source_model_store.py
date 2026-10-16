"""Tahap B source models for the app: build, list and read them.

The models are built by ``scripts/build_source_model.py`` in the OpenQuake
environment (HMTK); the app only starts that script and reads its CSV and
JSON outputs in ``data/psha/source_models/<name>/``.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE_MODELS_DIR = ROOT / "data" / "psha" / "source_models"
CATALOG_PATH = ROOT / "data" / "psha" / "catalog" / "mw_catalog.csv"
BUILD_SCRIPT = ROOT / "scripts" / "build_source_model.py"
WORKBOOK = "analisis_model_sumber.xlsx"
# A national box with many faults takes a few minutes; small boxes far less.
BUILD_TIMEOUT_SECONDS = 1800
# start_year/end_year None: every catalogue year (Stepp still sets completeness).
DEFAULT_OPTIONS = {"buffer_km": 300.0, "min_mw": 4.5, "smoothing_km": 50.0,
                   "mmax_shallow": 6.5, "mmax_slab": 7.8, "include_unreviewed": False,
                   "start_year": None, "end_year": None}


def normalize_options(options: dict) -> dict[str, Any]:
    """Builder options with their types (floats, a bool, years as int or None)."""
    opts = {**DEFAULT_OPTIONS, **options}
    years = {key: None if opts[key] is None else int(opts[key])
             for key in ("start_year", "end_year")}
    return {**{key: float(opts[key]) for key in
               ("buffer_km", "min_mw", "smoothing_km", "mmax_shallow", "mmax_slab")},
            "include_unreviewed": bool(opts["include_unreviewed"]), **years}


class SourceModelError(RuntimeError):
    """A source model cannot be built or read."""


@dataclass(frozen=True)
class SourceModel:
    name: str
    path: Path
    manifest: dict[str, Any]

    @property
    def ready(self) -> bool:
        return (self.path / "ssmLT.xml").is_file()

    def table(self, filename: str) -> pd.DataFrame:
        path = self.path / filename
        if not path.is_file():
            raise SourceModelError(f"{filename} tidak ada di model sumber {self.name}.")
        return pd.read_csv(path)

    @property
    def options(self) -> dict[str, Any]:
        """The builder options the model was made with (keys of ``DEFAULT_OPTIONS``)."""
        settings = self.manifest.get("settings", {})
        found = {"buffer_km": self.manifest.get("buffer_km"),
                 "min_mw": settings.get("min_mw_rates"),
                 "smoothing_km": settings.get("smoothing_km"),
                 "mmax_shallow": settings.get("mmax_shallow_background"),
                 "mmax_slab": settings.get("mmax_intraslab_background"),
                 "include_unreviewed": settings.get("include_unreviewed")}
        options = {key: value for key, value in found.items() if value is not None}
        # Models built before the year limits existed used every year.
        options["start_year"] = settings.get("start_year_limit")
        options["end_year"] = settings.get("end_year_limit")
        return normalize_options(options)

    @property
    def tectonic_regions(self) -> list[str]:
        summary = self.table("source_model_summary.csv")
        built = summary[summary["n_sources"].fillna(0) > 0]
        return sorted({str(t) for t in built["tectonic_region"].dropna() if str(t)})


def model_name(project_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", f"{project_id}_sumber").strip("_")


def bounds_from_sites(sites: list[tuple[float, float]], spacing: float) -> tuple:
    """Box around the sites, half a grid step beyond the outer ones."""
    lons = [s[0] for s in sites]
    lats = [s[1] for s in sites]
    half = spacing / 2
    return (round(min(lons) - half, 4), round(min(lats) - half, 4),
            round(max(lons) + half, 4), round(max(lats) + half, 4))


def load_source_model(name: str, root: Path = SOURCE_MODELS_DIR) -> SourceModel | None:
    path = root / name
    manifest_path = path / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SourceModelError(f"manifest.json model {name} tidak terbaca: {exc}") from exc
    return SourceModel(name, path, manifest)


def build_command(name: str, bounds: tuple, options: dict, python: Path) -> list[str]:
    opts = normalize_options(options)
    command = [str(python), str(BUILD_SCRIPT), "--name", name,
               "--bounds", *(f"{v:g}" for v in bounds),
               "--buffer-km", f"{opts['buffer_km']:g}", "--min-mw", f"{opts['min_mw']:g}",
               "--smoothing-km", f"{opts['smoothing_km']:g}",
               "--mmax-shallow", f"{opts['mmax_shallow']:g}",
               "--mmax-slab", f"{opts['mmax_slab']:g}"]
    for key, flag in (("start_year", "--start-year"), ("end_year", "--end-year")):
        if opts[key] is not None:
            command += [flag, str(opts[key])]
    if opts["include_unreviewed"]:
        command.append("--include-unreviewed")
    return command


def build_source_model(name: str, bounds: tuple, options: dict, python: Path,
                       root: Path = SOURCE_MODELS_DIR) -> SourceModel:
    """Run the builder (blocking) and return the finished model."""
    if not python.is_file():
        raise SourceModelError(f"Lingkungan OpenQuake tidak ditemukan: {python}")
    if not CATALOG_PATH.is_file():
        raise SourceModelError("Katalog Mw gabungan belum ada. Jalankan "
                               "scripts/setup_psha_data.py --only catalog di lingkungan "
                               "OpenQuake.")
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "SSLKEYLOGFILE")}
    try:
        result = subprocess.run(build_command(name, bounds, options, python), cwd=ROOT,
                                env=env, capture_output=True, text=True,
                                timeout=BUILD_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SourceModelError(f"Pembangun model sumber gagal dijalankan: {exc}") from exc
    if result.returncode != 0:
        tail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-12:])
        raise SourceModelError(f"Pembangun model sumber berhenti dengan galat:\n{tail}")
    model = load_source_model(name, root)
    if model is None or not model.ready:
        raise SourceModelError("Model sumber tidak lengkap setelah dibangun.")
    return model
