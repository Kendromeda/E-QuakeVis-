from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from utils.region_data import associate_coordinates_with_provinces


FAULT_COLUMNS = [
    "sesar",
    "segmen_sesar",
    "jarak_sesar_km",
    "wilayah_sesar",
    "tipe_sesar",
    "mmax_sesar",
    "sliprate_sesar",
]
FAULT_PROVINCE_SAMPLE_SPACING_DEGREES = 0.5
FAULT_MAX_OFFSHORE_ASSOCIATION_KM = 150.0
FAULT_DISTANCE_DECIMALS = 1


class FaultDataError(ValueError):
    """Raised when a fault reference file is missing or malformed."""


def load_fault_geojson(path: Path) -> dict[str, Any]:
    """Load and validate a local fault GeoJSON FeatureCollection."""
    if not path.exists():
        raise FaultDataError(f"Data sesar tidak ditemukan: {path.name}.")

    try:
        with path.open("r", encoding="utf-8") as file:
            geojson = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise FaultDataError("Data sesar tidak dapat dibaca sebagai GeoJSON.") from exc

    if geojson.get("type") != "FeatureCollection":
        raise FaultDataError("Data sesar harus berupa GeoJSON FeatureCollection.")

    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise FaultDataError("Data sesar tidak memiliki fitur garis.")

    return geojson


def _iter_line_strings(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])

    if geometry_type == "LineString":
        yield coordinates
    elif geometry_type == "MultiLineString":
        yield from coordinates


def get_fault_names(
    fault_geojson: dict[str, Any],
    feature_indexes: Iterable[int] | None = None,
) -> list[str]:
    """Return unique, displayable fault names."""
    selected_indexes = None if feature_indexes is None else set(feature_indexes)
    return sorted(
        {
            str(name)
            for feature_index, feature in enumerate(fault_geojson.get("features", []))
            if selected_indexes is None or feature_index in selected_indexes
            if (name := feature.get("properties", {}).get("Name"))
        }
    )


def get_fault_segment_options(
    fault_geojson: dict[str, Any],
    fault_names: Iterable[str],
) -> dict[int, str]:
    """Return selectable feature indexes and labels for the named faults.

    A feature index is used as the stable internal value because segment labels
    are not globally unique in the PUSGEN dataset.  The displayed value remains
    the concise segment name requested by the map filter.
    """
    selected_names = {str(name) for name in fault_names}
    raw_options: list[tuple[int, str, str]] = []
    for feature_index, feature in enumerate(fault_geojson.get("features", [])):
        properties = feature.get("properties", {})
        fault_name = properties.get("Name")
        if not fault_name or str(fault_name) not in selected_names:
            continue
        if feature.get("geometry", {}).get("type") not in {
            "LineString",
            "MultiLineString",
        }:
            continue
        segment_name = properties.get("Segment") or f"Segmen {feature_index + 1}"
        raw_options.append(
            (feature_index, str(fault_name), str(segment_name))
        )

    options: dict[int, str] = {}
    for feature_index, fault_name, segment_name in sorted(
        raw_options,
        key=lambda item: (item[2].casefold(), item[1].casefold(), item[0]),
    ):
        options[feature_index] = segment_name
    return options


def _sample_line(
    line: list[list[float]],
    spacing_degrees: float,
) -> Iterable[tuple[float, float]]:
    if not line:
        return

    last_sample = (float(line[0][0]), float(line[0][1]))
    yield last_sample
    distance_since_sample = 0.0
    for start, end in zip(line, line[1:]):
        start_lon, start_lat = float(start[0]), float(start[1])
        end_lon, end_lat = float(end[0]), float(end[1])
        segment_length = math.hypot(end_lon - start_lon, end_lat - start_lat)
        if segment_length == 0:
            continue
        travelled = spacing_degrees - distance_since_sample
        while travelled <= segment_length:
            ratio = travelled / segment_length
            last_sample = (
                start_lon + (end_lon - start_lon) * ratio,
                start_lat + (end_lat - start_lat) * ratio,
            )
            yield last_sample
            travelled += spacing_degrees
        distance_since_sample = segment_length - (travelled - spacing_degrees)

    end_point = (float(line[-1][0]), float(line[-1][1]))
    if end_point != last_sample:
        yield end_point


def build_fault_province_index(
    fault_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
) -> dict[str, list[int]]:
    """Index onshore and nearby offshore fault features by province."""
    coordinates: list[tuple[float, float]] = []
    feature_indexes: list[int] = []
    for feature_index, feature in enumerate(fault_geojson.get("features", [])):
        name = feature.get("properties", {}).get("Name")
        if not name:
            continue
        for line in _iter_line_strings(feature.get("geometry") or {}):
            for coordinate in _sample_line(
                line,
                FAULT_PROVINCE_SAMPLE_SPACING_DEGREES,
            ):
                coordinates.append(coordinate)
                feature_indexes.append(feature_index)

    province_names = associate_coordinates_with_provinces(
        coordinates,
        province_geojson,
        max_offshore_distance_km=FAULT_MAX_OFFSHORE_ASSOCIATION_KM,
    )
    indexed_features: dict[str, set[int]] = {}
    for province_name, feature_index in zip(province_names, feature_indexes):
        if province_name:
            indexed_features.setdefault(province_name, set()).add(feature_index)
    return {
        province_name: sorted(indexes)
        for province_name, indexes in sorted(indexed_features.items())
    }


def _prepare_segments(
    features: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    starts: list[list[float]] = []
    ends: list[list[float]] = []
    feature_indexes: list[int] = []

    for feature_index, feature in enumerate(features):
        for line in _iter_line_strings(feature["geometry"]):
            for start, end in zip(line, line[1:]):
                starts.append(start[:2])
                ends.append(end[:2])
                feature_indexes.append(feature_index)

    if not starts:
        raise FaultDataError("Data sesar tidak memiliki ruas garis yang valid.")

    return (
        np.asarray(starts, dtype=float),
        np.asarray(ends, dtype=float),
        np.asarray(feature_indexes, dtype=int),
    )


def _nearest_segment(
    latitude: float,
    longitude: float,
    starts: np.ndarray,
    ends: np.ndarray,
) -> tuple[int, float]:
    """Return the nearest trace segment and horizontal surface distance in km.

    GeoJSON fault vertices are longitude/latitude coordinates.  For every segment
    we project both endpoints to one local tangent plane centred on the epicentre.
    The scale factors follow the WGS84 ellipsoid at the mean latitude between the
    epicentre and segment, which is materially more accurate than fixed
    kilometres-per-degree constants while retaining the vectorised hot path.

    This is a 2-D epicentral distance to the mapped *surface trace*.  It is not a
    hypocentral distance to a dipping fault plane and must not be interpreted as
    evidence that the nearest named fault caused the earthquake.
    """
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise FaultDataError("Koordinat gempa harus berupa angka yang valid.")

    segment_latitudes = (starts[:, 1] + ends[:, 1]) / 2.0
    metric_latitudes = np.radians((latitude + segment_latitudes) / 2.0)
    latitude_scale = (
        111132.92
        - 559.82 * np.cos(2.0 * metric_latitudes)
        + 1.175 * np.cos(4.0 * metric_latitudes)
        - 0.0023 * np.cos(6.0 * metric_latitudes)
    ) / 1000.0
    longitude_scale = (
        111412.84 * np.cos(metric_latitudes)
        - 93.5 * np.cos(3.0 * metric_latitudes)
        + 0.118 * np.cos(5.0 * metric_latitudes)
    ) / 1000.0

    # Use the shortest signed longitude difference so a valid segment around the
    # antimeridian is not mistaken for a nearly world-spanning segment.
    start_longitude_delta = (starts[:, 0] - longitude + 180.0) % 360.0 - 180.0
    end_longitude_delta = (ends[:, 0] - longitude + 180.0) % 360.0 - 180.0
    start_x = start_longitude_delta * longitude_scale
    start_y = (starts[:, 1] - latitude) * latitude_scale
    end_x = end_longitude_delta * longitude_scale
    end_y = (ends[:, 1] - latitude) * latitude_scale

    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length_squared = delta_x * delta_x + delta_y * delta_y
    projection = np.divide(
        -(start_x * delta_x + start_y * delta_y),
        length_squared,
        out=np.zeros_like(length_squared),
        where=length_squared != 0,
    )
    projection = np.clip(projection, 0.0, 1.0)
    closest_x = start_x + projection * delta_x
    closest_y = start_y + projection * delta_y
    distance_squared = closest_x * closest_x + closest_y * closest_y
    nearest_index = int(np.argmin(distance_squared))
    return nearest_index, float(math.sqrt(distance_squared[nearest_index]))


def enrich_earthquakes_with_faults(
    df: pd.DataFrame,
    fault_geojson: dict[str, Any],
) -> pd.DataFrame:
    """Attach the geometrically nearest PUSGEN trace and 2-D distance.

    The returned association describes proximity only.  It does not identify the
    causative fault, because hypocentral depth, fault dip, mechanism, uncertainty,
    and the subsurface fault plane are not part of this calculation.
    """
    enriched = df.reset_index(drop=True).copy()
    if enriched.empty:
        for column in FAULT_COLUMNS:
            dtype = "float64" if column == "jarak_sesar_km" else "object"
            enriched[column] = pd.Series(dtype=dtype)
        return enriched

    features = [
        feature
        for feature in fault_geojson.get("features", [])
        if feature.get("geometry", {}).get("type") in {"LineString", "MultiLineString"}
    ]
    if not features:
        raise FaultDataError("Data sesar tidak memiliki geometri garis yang valid.")

    starts, ends, feature_indexes = _prepare_segments(features)
    nearest_records: list[dict[str, Any]] = []
    for row in enriched.itertuples(index=False):
        nearest_index, best_distance = _nearest_segment(
            float(row.lintang),
            float(row.bujur),
            starts,
            ends,
        )
        best_feature = features[int(feature_indexes[nearest_index])]
        properties = best_feature.get("properties", {})
        nearest_records.append(
            {
                "sesar": properties.get("Name") or "Tanpa nama",
                "segmen_sesar": properties.get("Segment") or "-",
                # The mapped trace and event location do not justify metre-level
                # display precision.  Keep filtering calculations unrounded in
                # ``distance_to_faults`` but publish proximity at 0.1 km.
                "jarak_sesar_km": round(
                    best_distance,
                    FAULT_DISTANCE_DECIMALS,
                ),
                "wilayah_sesar": properties.get("Region") or "-",
                "tipe_sesar": properties.get("Type") or "-",
                "mmax_sesar": properties.get("Mmax"),
                "sliprate_sesar": properties.get("Sliprate_m") or "-",
            }
        )

    return pd.concat([enriched, pd.DataFrame(nearest_records)], axis=1)


def distance_to_faults(
    df: pd.DataFrame,
    fault_geojson: dict[str, Any],
    fault_names: Iterable[str],
) -> np.ndarray:
    """Horizontal surface distance to the nearest mapped trace of the named faults.

    Unlike ``jarak_sesar_km`` (distance to whichever trace is globally nearest),
    this measures proximity to a *chosen* trace for spatial filtering.  Proximity
    alone is not evidence that an event originated on that fault.
    """
    selected_names = {str(name) for name in fault_names}
    features = [
        feature
        for feature in fault_geojson.get("features", [])
        if feature.get("geometry", {}).get("type") in {"LineString", "MultiLineString"}
        if str(feature.get("properties", {}).get("Name")) in selected_names
    ]
    if df.empty or not features:
        return np.full(len(df), np.nan, dtype=float)

    starts, ends, _ = _prepare_segments(features)
    distances = np.empty(len(df), dtype=float)
    for position, row in enumerate(df.itertuples(index=False)):
        _, distances[position] = _nearest_segment(
            float(row.lintang),
            float(row.bujur),
            starts,
            ends,
        )
    return distances


def distance_to_fault_segments(
    df: pd.DataFrame,
    fault_geojson: dict[str, Any],
    feature_indexes: Iterable[int],
) -> np.ndarray:
    """Distance to the nearest explicitly selected fault-segment feature.

    The calculation uses only the supplied GeoJSON feature indexes.  This keeps
    a segment filter independent from the earthquake's globally nearest fault
    metadata and lets several selected faults contribute all of their segments.
    """
    selected_indexes = {int(index) for index in feature_indexes}
    features = [
        feature
        for feature_index, feature in enumerate(fault_geojson.get("features", []))
        if feature_index in selected_indexes
        if feature.get("geometry", {}).get("type") in {
            "LineString",
            "MultiLineString",
        }
    ]
    if df.empty or not features:
        return np.full(len(df), np.nan, dtype=float)

    starts, ends, _ = _prepare_segments(features)
    distances = np.empty(len(df), dtype=float)
    for position, row in enumerate(df.itertuples(index=False)):
        _, distances[position] = _nearest_segment(
            float(row.lintang),
            float(row.bujur),
            starts,
            ends,
        )
    return distances
