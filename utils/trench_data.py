from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import shapefile


class TrenchDataError(ValueError):
    """Raised when a trench shapefile cannot be loaded safely."""


def load_trench_geojson(path: Path) -> dict[str, Any]:
    """Load a WGS84 trench line GeoJSON as a validated FeatureCollection.

    The committed snapshot is derived from the USGS Slab2 global trench axis
    (see ``scripts/build_sunda_trench_geojson.py``). Each feature keeps its own
    ``Name``; the map layer reads that and the line geometry directly.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrenchDataError("Data trench tidak dapat dibaca sebagai GeoJSON.") from exc

    if payload.get("type") != "FeatureCollection" or not isinstance(
        payload.get("features"), list
    ):
        raise TrenchDataError("Data trench harus berupa GeoJSON FeatureCollection.")

    line_types = {"LineString", "MultiLineString"}
    features = [
        feature
        for feature in payload["features"]
        if (feature.get("geometry") or {}).get("type") in line_types
        and (feature.get("geometry") or {}).get("coordinates")
    ]
    if not features:
        raise TrenchDataError("Data trench tidak memiliki geometri garis yang valid.")
    for feature in features:
        feature.setdefault("properties", {}).setdefault("Name", "Trench")
    return {"type": "FeatureCollection", "features": features}


def load_trench_shapefile(path: Path) -> dict[str, Any]:
    """Load a WGS84 polyline shapefile as a GeoJSON feature collection."""
    required_paths = [
        path,
        path.with_suffix(".shx"),
        path.with_suffix(".dbf"),
        path.with_suffix(".prj"),
    ]
    missing = [required.name for required in required_paths if not required.is_file()]
    if missing:
        raise TrenchDataError(
            "Komponen shapefile trench tidak lengkap: " + ", ".join(missing)
        )

    projection = path.with_suffix(".prj").read_text(
        encoding="utf-8",
        errors="ignore",
    )
    if "WGS_1984" not in projection and "WGS 84" not in projection:
        raise TrenchDataError("Shapefile trench harus menggunakan koordinat WGS84.")

    try:
        reader = shapefile.Reader(str(path))
    except (OSError, shapefile.ShapefileException) as exc:
        raise TrenchDataError("Shapefile trench tidak dapat dibaca.") from exc

    if reader.shapeType not in {
        shapefile.POLYLINE,
        shapefile.POLYLINEM,
        shapefile.POLYLINEZ,
    }:
        raise TrenchDataError("Geometri trench harus berupa garis.")

    features: list[dict[str, Any]] = []
    try:
        for index, shape_record in enumerate(reader.iterShapeRecords()):
            geometry = shape_record.shape.__geo_interface__
            if geometry.get("type") not in {"LineString", "MultiLineString"}:
                continue
            properties = shape_record.record.as_dict()
            properties.update(
                {
                    "Name": "Sumatra-Java Trench",
                    "Type": "Trench",
                    "FeatureId": index,
                }
            )
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": properties,
                }
            )
    except (OSError, shapefile.ShapefileException, ValueError) as exc:
        raise TrenchDataError("Geometri trench tidak valid.") from exc
    finally:
        reader.close()

    if not features:
        raise TrenchDataError("Shapefile trench tidak berisi geometri garis.")
    return {"type": "FeatureCollection", "features": features}
