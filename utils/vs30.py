"""USGS global Vs30 (Heath et al., 2020) for PSHA sites.

``scripts/prepare_usgs_vs30.py`` crops the 30 arc-second USGS grid to the
Indonesian region and stores it as ``data/vs30/usgs_vs30_indonesia.npz``
(int16 m/s, rows from south to north). This module samples that grid at the
sites (nearest cell) and classifies the values by SNI 1726:2019 site class.

The USGS values come from a topographic-slope proxy, not measurements; they
describe likely site conditions, not a substitute for a site investigation.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VS30_PATH = ROOT / "data" / "vs30" / "usgs_vs30_indonesia.npz"
CITATION = ("Heath, D. C., Wald, D. J., Worden, C. B., Thompson, E. M., & Smoczyk, G. M. "
            "(2020). A global hybrid VS30 map with a topographic slope-based default and "
            "regional map insets. Earthquake Spectra, 36(3), 1570-1584.")
# SNI 1726:2019 site classes by Vs30 (m/s), lower bound inclusive.
SNI_CLASSES = (("SE (tanah lunak)", 0.0), ("SD (tanah sedang)", 175.0),
               ("SC (tanah keras/batuan lunak)", 350.0), ("SB (batuan)", 750.0),
               ("SA (batuan keras)", 1500.0))


class Vs30Error(ValueError):
    """The Vs30 grid is missing or does not cover the sites."""


def load_grid(path: Path = VS30_PATH) -> dict:
    if not path.is_file():
        raise Vs30Error("Grid Vs30 USGS belum disiapkan. Jalankan "
                        "scripts/prepare_usgs_vs30.py di lingkungan OpenQuake.")
    with np.load(path) as data:
        return {"vs30": data["vs30"], "lon0": float(data["lon0"]), "lat0": float(data["lat0"]),
                "step": float(data["step"])}


def sample(lons, lats, grid: dict) -> np.ndarray:
    """Vs30 (m/s) of the nearest grid cell for each site."""
    lons = np.asarray(lons, dtype=float)
    lats = np.asarray(lats, dtype=float)
    rows = np.rint((lats - grid["lat0"]) / grid["step"]).astype(int)
    cols = np.rint((lons - grid["lon0"]) / grid["step"]).astype(int)
    n_rows, n_cols = grid["vs30"].shape
    outside = (rows < 0) | (rows >= n_rows) | (cols < 0) | (cols >= n_cols)
    if outside.any():
        raise Vs30Error(f"{int(outside.sum())} situs berada di luar cakupan grid Vs30.")
    return grid["vs30"][rows, cols].astype(float)


def sni_class(vs30) -> np.ndarray:
    """SNI 1726:2019 site class label for each Vs30 value."""
    vs30 = np.asarray(vs30, dtype=float)
    labels = np.full(vs30.shape, SNI_CLASSES[0][0], dtype=object)
    for label, lower in SNI_CLASSES[1:]:
        labels[vs30 >= lower] = label
    return labels


def class_summary(vs30) -> pd.DataFrame:
    """Number and share of sites per SNI class."""
    labels = pd.Series(sni_class(vs30))
    counts = labels.value_counts()
    rows = [{"Kelas situs SNI": label, "Jumlah situs": int(counts.get(label, 0)),
             "Persen": round(100 * counts.get(label, 0) / max(len(labels), 1), 1)}
            for label, _ in SNI_CLASSES]
    return pd.DataFrame(rows)
