from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Collection, Iterable

import numpy as np
import pandas as pd


EARTH_MEAN_RADIUS_KM = 6_371.0088
VOLCANO_NAME_FIELD = "NAMA_OBJEK"
VOLCANO_AREA_NAME_FIELD = "display_name"


class VolcanoDataError(ValueError):
    """Raised when the local BIG volcano reference is missing or malformed."""


def _valid_point_feature(feature: dict[str, Any]) -> bool:
    geometry = feature.get("geometry") or {}
    coordinates = geometry.get("coordinates") or []
    if geometry.get("type") != "Point" or len(coordinates) < 2:
        return False
    try:
        longitude = float(coordinates[0])
        latitude = float(coordinates[1])
    except (TypeError, ValueError):
        return False
    return (
        np.isfinite(longitude)
        and np.isfinite(latitude)
        and -180.0 <= longitude <= 180.0
        and -90.0 <= latitude <= 90.0
    )


def _valid_area_feature(feature: dict[str, Any]) -> bool:
    geometry = feature.get("geometry") or {}
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        return False
    if not geometry.get("coordinates"):
        return False
    properties = feature.get("properties") or {}
    try:
        latitude = float(properties.get("volcano_latitude"))
        longitude = float(properties.get("volcano_longitude"))
    except (TypeError, ValueError):
        return False
    return (
        np.isfinite(longitude)
        and np.isfinite(latitude)
        and -180.0 <= longitude <= 180.0
        and -90.0 <= latitude <= 90.0
    )


def get_volcano_feature_name(feature: dict[str, Any]) -> str:
    """Return the display name shared by point and KRB-area datasets."""
    properties = feature.get("properties") or {}
    return str(
        properties.get(VOLCANO_AREA_NAME_FIELD)
        or properties.get(VOLCANO_NAME_FIELD)
        or properties.get("namobj")
        or ""
    ).strip()


def load_volcano_geojson(path: Path) -> dict[str, Any]:
    """Load and validate the local BIG volcano GeoJSON FeatureCollection."""
    if not path.exists():
        raise VolcanoDataError(f"Data gunung api tidak ditemukan: {path.name}.")

    try:
        with path.open("r", encoding="utf-8") as file:
            geojson = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise VolcanoDataError(
            "Data gunung api tidak dapat dibaca sebagai GeoJSON."
        ) from exc

    if geojson.get("type") != "FeatureCollection":
        raise VolcanoDataError(
            "Data gunung api harus berupa GeoJSON FeatureCollection."
        )

    features = geojson.get("features")
    if not isinstance(features, list) or not any(
        _valid_point_feature(feature) or _valid_area_feature(feature)
        for feature in features
    ):
        raise VolcanoDataError(
            "Data gunung api tidak memiliki fitur titik atau area KRB yang valid."
        )
    return geojson


def iter_volcano_points(
    volcano_geojson: dict[str, Any],
    volcano_names: Collection[str] | None = None,
) -> Iterable[tuple[dict[str, Any], float, float]]:
    """Yield valid volcano features as ``(feature, latitude, longitude)``."""
    selected_names = (
        None if volcano_names is None else {str(name) for name in volcano_names}
    )
    seen: set[tuple[str, float, float]] = set()
    for feature in volcano_geojson.get("features", []):
        name = get_volcano_feature_name(feature)
        if selected_names is not None and name not in selected_names:
            continue
        if _valid_point_feature(feature):
            coordinates = feature["geometry"]["coordinates"]
            latitude, longitude = float(coordinates[1]), float(coordinates[0])
        elif _valid_area_feature(feature):
            properties = feature.get("properties") or {}
            latitude = float(properties["volcano_latitude"])
            longitude = float(properties["volcano_longitude"])
        else:
            continue
        key = (name, latitude, longitude)
        if key in seen:
            continue
        seen.add(key)
        yield feature, latitude, longitude


def get_volcano_names(volcano_geojson: dict[str, Any]) -> list[str]:
    """Return unique volcano names suitable for the interactive selector."""
    names: set[str] = set()
    for feature in volcano_geojson.get("features", []):
        name = get_volcano_feature_name(feature)
        if name:
            names.add(name)
    return sorted(names, key=str.casefold)


def filter_volcano_features(
    volcano_geojson: dict[str, Any],
    volcano_names: Collection[str] | None,
) -> dict[str, Any]:
    """Return all volcano features or only explicitly selected names."""
    selected_names = (
        None if volcano_names is None else {str(name) for name in volcano_names}
    )
    return {
        "type": "FeatureCollection",
        "features": [
            feature
            for feature in volcano_geojson.get("features", [])
            if (
                selected_names is None
                or get_volcano_feature_name(feature) in selected_names
            )
            and (_valid_point_feature(feature) or _valid_area_feature(feature))
        ],
    }


def distance_to_volcanoes(
    df: pd.DataFrame,
    volcano_geojson: dict[str, Any],
    volcano_names: Collection[str],
) -> np.ndarray:
    """Great-circle surface distance to the nearest selected volcano, in km."""
    volcanoes = list(iter_volcano_points(volcano_geojson, volcano_names))
    if df.empty or not volcanoes:
        return np.full(len(df), np.nan, dtype=float)

    event_latitudes = np.radians(
        pd.to_numeric(df["lintang"], errors="coerce").to_numpy(dtype=float)
    )
    event_longitudes_degrees = pd.to_numeric(
        df["bujur"], errors="coerce"
    ).to_numpy(dtype=float)
    event_longitudes = np.radians(event_longitudes_degrees)
    minimum_distances = np.full(len(df), np.inf, dtype=float)

    for _feature, latitude, longitude in volcanoes:
        volcano_latitude = np.radians(latitude)
        volcano_longitude = np.radians(longitude)
        latitude_delta = volcano_latitude - event_latitudes
        longitude_delta = (
            volcano_longitude - event_longitudes + np.pi
        ) % (2.0 * np.pi) - np.pi
        haversine = (
            np.sin(latitude_delta / 2.0) ** 2
            + np.cos(event_latitudes)
            * np.cos(volcano_latitude)
            * np.sin(longitude_delta / 2.0) ** 2
        )
        angular_distance = 2.0 * np.arcsin(
            np.sqrt(np.clip(haversine, 0.0, 1.0))
        )
        minimum_distances = np.minimum(
            minimum_distances,
            EARTH_MEAN_RADIUS_KM * angular_distance,
        )

    invalid_coordinates = ~(
        np.isfinite(event_latitudes) & np.isfinite(event_longitudes_degrees)
    )
    minimum_distances[invalid_coordinates] = np.nan
    return minimum_distances
