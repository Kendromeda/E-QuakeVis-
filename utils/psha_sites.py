"""PSHA site grids shared by the app and ``scripts/run_psha.py``.

Sites are nodes of a regular lon/lat grid (multiples of ``spacing``) that fall
on land inside the selected provinces of ``data/provinsi_indonesia_38.geojson``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from shapely import contains_xy
from shapely.geometry import shape

ROOT = Path(__file__).resolve().parents[1]
PROVINCES_PATH = ROOT / "data" / "provinsi_indonesia_38.geojson"
PROVINCE_KEY = "PROVINSI"


def load_province_features(path: Path = PROVINCES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def province_names(features: list[dict[str, Any]]) -> list[str]:
    return sorted({f["properties"][PROVINCE_KEY] for f in features})


def build_sites(
    features: list[dict[str, Any]],
    provinces: Iterable[str] | None,
    spacing: float,
) -> list[tuple[float, float]]:
    """Grid nodes that fall on land in ``provinces`` (all provinces when empty)."""
    if spacing <= 0:
        raise ValueError("Jarak grid harus positif.")
    selected = set(provinces or ())
    if selected:
        unknown = sorted(selected - set(province_names(features)))
        if unknown:
            raise ValueError(f"Provinsi tidak dikenal: {', '.join(unknown)}")
        features = [f for f in features if f["properties"][PROVINCE_KEY] in selected]
    # Some source polygons are topologically invalid, so test each one instead
    # of unioning them.
    polygons = [shape(f["geometry"]) for f in features if f.get("geometry")]
    if not polygons:
        return []
    bounds = np.array([p.bounds for p in polygons])
    west, south = bounds[:, :2].min(axis=0)
    east, north = bounds[:, 2:].max(axis=0)
    lons = np.arange(math.floor(west / spacing), math.ceil(east / spacing) + 1) * spacing
    lats = np.arange(math.floor(south / spacing), math.ceil(north / spacing) + 1) * spacing
    grid_lon, grid_lat = np.meshgrid(lons, lats)
    inside = np.zeros(grid_lon.shape, dtype=bool)
    for polygon in polygons:
        inside |= contains_xy(polygon, grid_lon, grid_lat)
    return [(round(float(x), 4), round(float(y), 4))
            for x, y in zip(grid_lon[inside], grid_lat[inside])]
