"""PSHA site grids shared by the app and ``scripts/run_psha.py``.

Sites are nodes of a regular lon/lat grid (multiples of ``spacing``) that fall
on land inside the selected areas: provinces of
``data/provinsi_indonesia_38.geojson`` or regencies/cities (kabupaten/kota) of
``data/kabupaten_kota_indonesia.geojson``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from shapely import contains_xy
from shapely.geometry import box as shapely_box, shape

ROOT = Path(__file__).resolve().parents[1]
PROVINCES_PATH = ROOT / "data" / "provinsi_indonesia_38.geojson"
PROVINCE_KEY = "PROVINSI"
KABUPATEN_PATH = ROOT / "data" / "kabupaten_kota_indonesia.geojson"
KABUPATEN_KEY = "WADMKK"
KABUPATEN_PROVINCE_KEY = "WADMPR"


def load_province_features(path: Path = PROVINCES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def load_kabupaten_features(path: Path = KABUPATEN_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def _names(features: list[dict[str, Any]], key: str) -> list[str]:
    return sorted({f["properties"][key] for f in features if f["properties"].get(key)})


def province_names(features: list[dict[str, Any]]) -> list[str]:
    return _names(features, PROVINCE_KEY)


def kabupaten_names(features: list[dict[str, Any]], province: str | None = None) -> list[str]:
    """Kabupaten/kota names, optionally only those of one province."""
    if province:
        features = [f for f in features
                    if f["properties"].get(KABUPATEN_PROVINCE_KEY) == province]
    return _names(features, KABUPATEN_KEY)


def region_polygons(region: dict[str, Any]) -> list:
    """Shapely polygons of a run's region (manifest ``region``): its kabupaten,
    provinces (every province when "all" or empty), cut to its box if any."""
    if region.get("kabupaten"):
        names, features, key = set(region["kabupaten"]), load_kabupaten_features(), KABUPATEN_KEY
    else:
        provinces = region.get("provinces")
        names = set(provinces) if isinstance(provinces, list) and provinces else None
        features, key = load_province_features(), PROVINCE_KEY
    polygons = [shape(f["geometry"]) for f in features if f.get("geometry")
                and (names is None or f["properties"].get(key) in names)]
    if region.get("bounds"):
        box = shapely_box(*region["bounds"])
        polygons = [p.intersection(box) for p in polygons if p.intersects(box)]
    return [p for p in polygons if not p.is_empty]


def build_sites(
    features: list[dict[str, Any]],
    areas: Iterable[str] | None,
    spacing: float,
    key: str = PROVINCE_KEY,
    bounds: tuple[float, float, float, float] | None = None,
) -> list[tuple[float, float]]:
    """Grid nodes that fall on land in ``areas`` (every feature when empty).

    ``key`` is the property naming each area: ``PROVINCE_KEY`` for provinces,
    ``KABUPATEN_KEY`` for kabupaten/kota. ``bounds`` (west, south, east, north)
    keeps only nodes inside that box; the grid is built for the box only, so
    fine grids (1 km) stay fast.
    """
    if spacing <= 0:
        raise ValueError("Jarak grid harus positif.")
    selected = set(areas or ())
    if selected:
        unknown = sorted(selected - set(_names(features, key)))
        if unknown:
            raise ValueError(f"Wilayah tidak dikenal: {', '.join(unknown)}")
        features = [f for f in features if f["properties"].get(key) in selected]
    # Some source polygons are topologically invalid, so test each one instead
    # of unioning them.
    polygons = [shape(f["geometry"]) for f in features if f.get("geometry")]
    if bounds is not None:
        box = shapely_box(*bounds)
        polygons = [p for p in polygons if p.intersects(box)]
    if not polygons:
        return []
    extent = np.array([p.bounds for p in polygons])
    west, south = extent[:, :2].min(axis=0)
    east, north = extent[:, 2:].max(axis=0)
    if bounds is not None:
        west, south = max(west, bounds[0]), max(south, bounds[1])
        east, north = min(east, bounds[2]), min(north, bounds[3])
    lons = np.arange(math.floor(west / spacing), math.ceil(east / spacing) + 1) * spacing
    lats = np.arange(math.floor(south / spacing), math.ceil(north / spacing) + 1) * spacing
    grid_lon, grid_lat = np.meshgrid(lons, lats)
    inside = np.zeros(grid_lon.shape, dtype=bool)
    for polygon in polygons:
        inside |= contains_xy(polygon, grid_lon, grid_lat)
    if bounds is not None:
        inside &= ((grid_lon >= bounds[0] - 1e-9) & (grid_lon <= bounds[2] + 1e-9)
                   & (grid_lat >= bounds[1] - 1e-9) & (grid_lat <= bounds[3] + 1e-9))
    return [(round(float(x), 4), round(float(y), 4))
            for x, y in zip(grid_lon[inside], grid_lat[inside])]
