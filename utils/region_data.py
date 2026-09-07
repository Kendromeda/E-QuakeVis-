from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


OUTSIDE_REGION = "Di luar wilayah administrasi"
UNKNOWN_DISTRICT = "Tidak teridentifikasi"
LAND_LOCATION = "Darat"
SEA_LOCATION = "Laut"
MAX_OFFSHORE_ASSOCIATION_KM = 400.0


class RegionDataError(ValueError):
    """Raised when an administrative boundary file is missing or malformed."""


def load_region_geojson(path: Path) -> dict[str, Any]:
    """Load and validate a polygon GeoJSON FeatureCollection."""
    if not path.exists():
        raise RegionDataError(f"Data wilayah tidak ditemukan: {path.name}.")

    try:
        with path.open("r", encoding="utf-8") as file:
            geojson = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise RegionDataError("Data wilayah tidak dapat dibaca sebagai GeoJSON.") from exc

    if geojson.get("type") != "FeatureCollection":
        raise RegionDataError("Data wilayah harus berupa GeoJSON FeatureCollection.")

    features = geojson.get("features")
    if not isinstance(features, list) or not features:
        raise RegionDataError("Data wilayah tidak memiliki fitur poligon.")

    valid_types = {"Polygon", "MultiPolygon"}
    if not any(feature.get("geometry", {}).get("type") in valid_types for feature in features):
        raise RegionDataError("Data wilayah tidak memiliki geometri poligon yang valid.")

    return geojson


def _iter_polygons(geometry: dict[str, Any]) -> Iterable[list[list[list[float]]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        yield coordinates
    elif geometry_type == "MultiPolygon":
        yield from coordinates


def _point_on_segment(
    longitude: float,
    latitude: float,
    start: list[float],
    end: list[float],
) -> bool:
    start_lon, start_lat = float(start[0]), float(start[1])
    end_lon, end_lat = float(end[0]), float(end[1])
    cross = (longitude - start_lon) * (end_lat - start_lat) - (
        latitude - start_lat
    ) * (end_lon - start_lon)
    if abs(cross) > 1e-10:
        return False
    return (
        min(start_lon, end_lon) - 1e-10 <= longitude <= max(start_lon, end_lon) + 1e-10
        and min(start_lat, end_lat) - 1e-10
        <= latitude
        <= max(start_lat, end_lat) + 1e-10
    )


def _point_in_ring(
    longitude: float,
    latitude: float,
    ring: list[list[float]],
) -> bool:
    inside = False
    for start, end in zip(ring, ring[1:]):
        if _point_on_segment(longitude, latitude, start, end):
            return True

        start_lon, start_lat = float(start[0]), float(start[1])
        end_lon, end_lat = float(end[0]), float(end[1])
        crosses_latitude = (start_lat > latitude) != (end_lat > latitude)
        if not crosses_latitude:
            continue

        intersection_lon = start_lon + (
            (latitude - start_lat) * (end_lon - start_lon) / (end_lat - start_lat)
        )
        if longitude < intersection_lon:
            inside = not inside
    return inside


def _point_in_polygon(
    longitude: float,
    latitude: float,
    polygon: list[list[list[float]]],
) -> bool:
    if not polygon or not _point_in_ring(longitude, latitude, polygon[0]):
        return False
    return not any(
        _point_in_ring(longitude, latitude, hole)
        for hole in polygon[1:]
    )


def _prepare_regions(
    geojson: dict[str, Any],
    name_field: str,
    province_field: str,
) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for feature in geojson.get("features", []):
        geometry = feature.get("geometry") or {}
        polygons = list(_iter_polygons(geometry))
        if not polygons:
            continue

        points = [point for polygon in polygons for ring in polygon for point in ring]
        properties = feature.get("properties", {})
        prepared.append(
            {
                "name": properties.get(name_field),
                "province": properties.get(province_field),
                "polygons": polygons,
                "bbox": (
                    min(float(point[0]) for point in points),
                    min(float(point[1]) for point in points),
                    max(float(point[0]) for point in points),
                    max(float(point[1]) for point in points),
                ),
            }
        )
    return prepared


def _build_region_grid(
    regions: list[dict[str, Any]],
    cell_size: float = 1.0,
) -> dict[tuple[int, int], list[int]]:
    grid: dict[tuple[int, int], list[int]] = {}
    for region_index, region in enumerate(regions):
        min_lon, min_lat, max_lon, max_lat = region["bbox"]
        min_x = math.floor(min_lon / cell_size)
        max_x = math.floor(max_lon / cell_size)
        min_y = math.floor(min_lat / cell_size)
        max_y = math.floor(max_lat / cell_size)
        for cell_x in range(min_x, max_x + 1):
            for cell_y in range(min_y, max_y + 1):
                grid.setdefault((cell_x, cell_y), []).append(region_index)
    return grid


def _find_region(
    longitude: float,
    latitude: float,
    regions: list[dict[str, Any]],
    grid: dict[tuple[int, int], list[int]] | None = None,
    cell_size: float = 1.0,
) -> dict[str, Any] | None:
    candidate_indexes = (
        grid.get(
            (
                math.floor(longitude / cell_size),
                math.floor(latitude / cell_size),
            ),
            [],
        )
        if grid is not None
        else range(len(regions))
    )
    for region_index in candidate_indexes:
        region = regions[region_index]
        min_lon, min_lat, max_lon, max_lat = region["bbox"]
        if not (min_lon <= longitude <= max_lon and min_lat <= latitude <= max_lat):
            continue
        if any(
            _point_in_polygon(longitude, latitude, polygon)
            for polygon in region["polygons"]
        ):
            return region
    return None


def _prepare_region_segments(
    regions: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    starts: list[list[float]] = []
    ends: list[list[float]] = []
    region_indexes: list[int] = []
    for region_index, region in enumerate(regions):
        for polygon in region["polygons"]:
            for ring in polygon:
                for start, end in zip(ring, ring[1:]):
                    starts.append(start[:2])
                    ends.append(end[:2])
                    region_indexes.append(region_index)

    if not starts:
        raise RegionDataError("Data provinsi tidak memiliki batas poligon yang valid.")
    return (
        np.asarray(starts, dtype=float),
        np.asarray(ends, dtype=float),
        np.asarray(region_indexes, dtype=int),
    )


def _region_segment_distances(
    longitude: float,
    latitude: float,
    starts: np.ndarray,
    ends: np.ndarray,
    candidate_segments: np.ndarray,
) -> np.ndarray:
    candidate_starts = starts[candidate_segments]
    candidate_ends = ends[candidate_segments]
    longitude_scale = 111.320 * math.cos(math.radians(latitude))
    start_x = (candidate_starts[:, 0] - longitude) * longitude_scale
    start_y = (candidate_starts[:, 1] - latitude) * 110.574
    end_x = (candidate_ends[:, 0] - longitude) * longitude_scale
    end_y = (candidate_ends[:, 1] - latitude) * 110.574
    delta_x = end_x - start_x
    delta_y = end_y - start_y
    length_squared = delta_x * delta_x + delta_y * delta_y
    projection = np.divide(
        -(start_x * delta_x + start_y * delta_y),
        length_squared,
        out=np.zeros_like(length_squared),
        where=length_squared != 0,
    )
    np.clip(projection, 0.0, 1.0, out=projection)
    closest_x = start_x + projection * delta_x
    closest_y = start_y + projection * delta_y
    return closest_x * closest_x + closest_y * closest_y


def _nearest_regions_indexed(
    longitudes: np.ndarray,
    latitudes: np.ndarray,
    regions: list[dict[str, Any]],
    starts: np.ndarray,
    ends: np.ndarray,
    region_indexes: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    bounds = np.asarray([region["bbox"] for region in regions], dtype=float)
    segments_by_region = [
        np.flatnonzero(region_indexes == region_index)
        for region_index in range(len(regions))
    ]
    nearest_regions = np.empty(len(latitudes), dtype=int)
    nearest_distances = np.empty(len(latitudes), dtype=float)

    for point_index, (longitude, latitude) in enumerate(
        zip(longitudes, latitudes)
    ):
        longitude_scale = 111.320 * math.cos(math.radians(float(latitude)))
        longitude_delta = np.maximum(
            np.maximum(bounds[:, 0] - longitude, longitude - bounds[:, 2]),
            0.0,
        ) * longitude_scale
        latitude_delta = np.maximum(
            np.maximum(bounds[:, 1] - latitude, latitude - bounds[:, 3]),
            0.0,
        ) * 110.574
        lower_bounds = longitude_delta * longitude_delta + latitude_delta * latitude_delta

        initial_count = min(3, len(regions))
        initial_regions = np.argpartition(lower_bounds, initial_count - 1)[:initial_count]
        initial_segments = np.concatenate(
            [segments_by_region[int(index)] for index in initial_regions]
        )
        initial_distances = _region_segment_distances(
            float(longitude),
            float(latitude),
            starts,
            ends,
            initial_segments,
        )
        best_initial = float(initial_distances.min())
        candidate_regions = np.flatnonzero(lower_bounds <= best_initial + 1e-12)
        candidate_segments = np.concatenate(
            [segments_by_region[int(index)] for index in candidate_regions]
        )
        candidate_distances = _region_segment_distances(
            float(longitude),
            float(latitude),
            starts,
            ends,
            candidate_segments,
        )
        local_segment = int(np.argmin(candidate_distances))
        nearest_segment = int(candidate_segments[local_segment])
        nearest_regions[point_index] = int(region_indexes[nearest_segment])
        nearest_distances[point_index] = math.sqrt(
            float(candidate_distances[local_segment])
        )

    return nearest_regions, nearest_distances


def associate_coordinates_with_provinces(
    coordinates: Iterable[tuple[float, float]],
    province_geojson: dict[str, Any],
    max_offshore_distance_km: float = MAX_OFFSHORE_ASSOCIATION_KM,
) -> list[str | None]:
    """Associate land and nearby offshore coordinates with province names."""
    coordinate_list = list(coordinates)
    if not coordinate_list:
        return []

    provinces = _prepare_regions(province_geojson, "PROVINSI", "PROVINSI")
    if not provinces:
        raise RegionDataError("Data provinsi tidak memiliki fitur yang valid.")

    province_grid = _build_region_grid(provinces)
    province_starts, province_ends, province_indexes = _prepare_region_segments(
        provinces
    )
    matches: list[str | None] = [None] * len(coordinate_list)
    offshore_indexes: list[int] = []
    offshore_longitudes: list[float] = []
    offshore_latitudes: list[float] = []

    for index, (longitude, latitude) in enumerate(coordinate_list):
        province = _find_region(
            float(longitude),
            float(latitude),
            provinces,
            province_grid,
        )
        if province is not None:
            matches[index] = province.get("name")
            continue
        offshore_indexes.append(index)
        offshore_longitudes.append(float(longitude))
        offshore_latitudes.append(float(latitude))

    if offshore_indexes:
        nearest_indexes, nearest_distances = _nearest_regions_indexed(
            np.asarray(offshore_longitudes, dtype=float),
            np.asarray(offshore_latitudes, dtype=float),
            provinces,
            province_starts,
            province_ends,
            province_indexes,
        )
        for coordinate_index, province_index, distance in zip(
            offshore_indexes,
            nearest_indexes,
            nearest_distances,
        ):
            if float(distance) <= max_offshore_distance_km:
                matches[coordinate_index] = provinces[int(province_index)].get("name")

    return matches


def enrich_earthquakes_with_regions(
    df: pd.DataFrame,
    province_geojson: dict[str, Any],
    district_geojson: dict[str, Any],
) -> pd.DataFrame:
    """Attach land/sea classification and the containing or nearest province."""
    enriched = df.reset_index(drop=True).copy()
    if enriched.empty:
        enriched["provinsi"] = pd.Series(dtype="object")
        enriched["kabupaten_kota"] = pd.Series(dtype="object")
        enriched["jenis_lokasi"] = pd.Series(dtype="object")
        enriched["jarak_darat_km"] = pd.Series(dtype="float64")
        return enriched

    provinces = _prepare_regions(province_geojson, "PROVINSI", "PROVINSI")
    districts = _prepare_regions(district_geojson, "WADMKK", "WADMPR")
    if len(provinces) != 38:
        raise RegionDataError(
            f"Data provinsi harus berisi 38 fitur, ditemukan {len(provinces)}."
        )
    province_starts, province_ends, province_indexes = _prepare_region_segments(
        provinces
    )
    province_grid = _build_region_grid(provinces)
    district_grid = _build_region_grid(districts)

    matches: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []
    sea_row_indexes: list[int] = []
    sea_longitudes: list[float] = []
    sea_latitudes: list[float] = []
    for row_index, row in enumerate(enriched.itertuples(index=False)):
        longitude = float(row.bujur)
        latitude = float(row.lintang)
        # The province polygon is the authoritative land mask. District
        # boundaries are used only to attach the kabupaten/kota name, never to
        # decide whether a point is on land or at sea.
        province = _find_region(
            longitude,
            latitude,
            provinces,
            province_grid,
        )
        district = (
            _find_region(
                longitude,
                latitude,
                districts,
                district_grid,
            )
            if province is not None
            else None
        )

        matches.append((district, province))
        if province is None:
            sea_row_indexes.append(row_index)
            sea_longitudes.append(longitude)
            sea_latitudes.append(latitude)

    nearest_by_row: dict[int, tuple[dict[str, Any], float]] = {}
    if sea_row_indexes:
        nearest_region_indexes, nearest_distances = _nearest_regions_indexed(
            np.asarray(sea_longitudes, dtype=float),
            np.asarray(sea_latitudes, dtype=float),
            provinces,
            province_starts,
            province_ends,
            province_indexes,
        )
        nearest_by_row = {
            row_index: (provinces[int(region_index)], float(distance))
            for row_index, region_index, distance in zip(
                sea_row_indexes,
                nearest_region_indexes,
                nearest_distances,
            )
        }

    records: list[dict[str, Any]] = []
    for row_index, (district, province) in enumerate(matches):
        if province is not None:
            province_name = province.get("name")
            district_name = (district or {}).get("name")
            location_type = LAND_LOCATION
            land_distance_km = 0.0
        else:
            nearest_province, land_distance_km = nearest_by_row[row_index]
            location_type = SEA_LOCATION
            if land_distance_km <= MAX_OFFSHORE_ASSOCIATION_KM:
                province_name = nearest_province.get("name")
                district_name = (
                    f"Perairan {province_name}" if province_name else None
                )
            else:
                province_name = None
                district_name = None
        records.append(
            {
                "provinsi": province_name or OUTSIDE_REGION,
                "kabupaten_kota": district_name
                or (UNKNOWN_DISTRICT if province_name else OUTSIDE_REGION),
                "jenis_lokasi": location_type,
                "jarak_darat_km": round(land_distance_km, 2),
            }
        )

    enriched = enriched.drop(
        columns=["provinsi", "kabupaten_kota", "jenis_lokasi", "jarak_darat_km"],
        errors="ignore",
    )
    return pd.concat([enriched, pd.DataFrame(records)], axis=1)
