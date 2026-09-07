from __future__ import annotations

from pathlib import Path
from typing import Any, Collection


class SlabDataError(ValueError):
    """Raised when a Slab2 depth contour file cannot be loaded safely."""


def _parse_depth(header: str) -> float | None:
    """Extract the positive depth (km) from a GMT segment header.

    Headers look like ``> -660 contour -Z-660``; the value after ``-Z`` is the
    slab depth in kilometres (negative below sea level).
    """
    if "-Z" in header:
        try:
            return abs(float(header.split("-Z")[-1].strip()))
        except ValueError:
            pass
    for token in header[1:].split():
        try:
            return abs(float(token))
        except ValueError:
            continue
    return None


def load_slab_contours(path: Path) -> dict[str, Any]:
    """Parse a Slab2 GMT-format depth contour (.in) file into GeoJSON.

    Each ``>`` segment header carries the contour depth; the depth is stored as
    a positive kilometre value on the resulting LineString feature.
    """
    if not path.is_file():
        raise SlabDataError(f"Berkas kontur slab tidak ditemukan: {path.name}")

    features: list[dict[str, Any]] = []
    current_coords: list[list[float]] = []
    current_depth: float | None = None

    def flush() -> None:
        nonlocal current_coords
        if current_depth is not None and len(current_coords) >= 2:
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": current_coords,
                    },
                    "properties": {"depth": current_depth},
                }
            )
        current_coords = []

    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith(">"):
                    flush()
                    current_depth = _parse_depth(line)
                    continue
                parts = line.replace(",", " ").split()
                if len(parts) < 2:
                    continue
                try:
                    longitude = float(parts[0])
                    latitude = float(parts[1])
                except ValueError:
                    continue
                current_coords.append([longitude, latitude])
    except OSError as exc:
        raise SlabDataError("Berkas kontur slab tidak dapat dibaca.") from exc

    flush()
    if not features:
        raise SlabDataError("Berkas kontur slab tidak berisi garis kontur.")
    return {"type": "FeatureCollection", "features": features}


def get_slab_depths(slab_geojson: dict[str, Any]) -> list[float]:
    """Return the sorted unique contour depths (km) present in the model."""
    return sorted(
        {
            float(depth)
            for feature in slab_geojson.get("features", [])
            if (depth := feature.get("properties", {}).get("depth")) is not None
        }
    )


def filter_slab_contours(
    slab_geojson: dict[str, Any],
    *,
    interval_km: int | None = None,
    depths: Collection[float] | None = None,
) -> dict[str, Any]:
    """Return a contour subset kept at ``interval_km`` steps or explicit depths."""
    selected = None if depths is None else {round(float(depth)) for depth in depths}
    features = [
        feature
        for feature in slab_geojson.get("features", [])
        if (depth := feature.get("properties", {}).get("depth")) is not None
        if selected is None or round(float(depth)) in selected
        if interval_km is None
        or interval_km <= 0
        or round(float(depth)) % interval_km == 0
    ]
    return {"type": "FeatureCollection", "features": features}
