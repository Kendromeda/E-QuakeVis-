"""Rectangle (bounding-box) selection drawn on the distribution map."""

from __future__ import annotations

import math
from typing import Any, Iterable

import pandas as pd

Bounds = tuple[float, float, float, float]


def parse_coordinate_bounds(
    north_text: str, west_text: str, east_text: str, south_text: str,
) -> Bounds:
    """Validate manual decimal degrees and return (west, south, east, north)."""
    values = {}
    for name, text in (
        ("North", north_text), ("West", west_text),
        ("East", east_text), ("South", south_text),
    ):
        try:
            value = float(str(text).strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError(f"{name} harus berupa angka derajat desimal.") from exc
        if not math.isfinite(value):
            raise ValueError(f"{name} harus berupa angka derajat desimal yang terbatas.")
        values[name] = value
    north, west, east, south = (values[name] for name in ("North", "West", "East", "South"))
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("North dan South harus berada di antara -90° dan 90°.")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("West dan East harus berada di antara -180° dan 180°.")
    if north <= south:
        raise ValueError("North harus lebih besar daripada South.")
    if east <= west:
        raise ValueError("East harus lebih besar daripada West.")
    return west, south, east, north


def _iter_positions(node: Any) -> Iterable[tuple[float, float]]:
    if isinstance(node, (list, tuple)):
        if (
            len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            yield float(node[0]), float(node[1])
        else:
            for child in node:
                yield from _iter_positions(child)


def bounds_from_drawing(drawing: Any) -> Bounds | None:
    """Return (west, south, east, north) of a drawn GeoJSON polygon feature."""
    if not isinstance(drawing, dict):
        return None
    geometry = drawing.get("geometry") if drawing.get("type") == "Feature" else drawing
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        return None
    positions = list(_iter_positions(geometry.get("coordinates")))
    if len(positions) < 3:
        return None
    if any(
        not math.isfinite(longitude)
        or not math.isfinite(latitude)
        or not (-180 <= longitude <= 180)
        or not (-90 <= latitude <= 90)
        for longitude, latitude in positions
    ):
        return None
    longitudes = [position[0] for position in positions]
    latitudes = [position[1] for position in positions]
    west, east = min(longitudes), max(longitudes)
    south, north = min(latitudes), max(latitudes)
    if east <= west or north <= south:
        return None
    return round(west, 4), round(south, 4), round(east, 4), round(north, 4)


def latest_drawing_bounds(drawings: Any) -> Bounds | None:
    """Bounds of the most recently drawn rectangle in a Leaflet.Draw list."""
    if not isinstance(drawings, list):
        return None
    for drawing in reversed(drawings):
        bounds = bounds_from_drawing(drawing)
        if bounds is not None:
            return bounds
    return None


def filter_by_bounds(df: pd.DataFrame, bounds: Bounds | None) -> pd.DataFrame:
    """Keep epicentres inside the box (edges inclusive)."""
    if bounds is None or df.empty:
        return df
    west, south, east, north = bounds
    longitude = pd.to_numeric(df["bujur"], errors="coerce")
    latitude = pd.to_numeric(df["lintang"], errors="coerce")
    mask = longitude.between(west, east) & latitude.between(south, north)
    return df.loc[mask]


def format_bounds(bounds: Bounds) -> str:
    """Human-readable box label, e.g. ``95.00° BT – 100.50° BT, 2.00° LS – 4.00° LU``."""
    west, south, east, north = bounds

    def decimal(value: float) -> str:
        displayed = f"{abs(value):.4f}".rstrip("0")
        fractional = len(displayed.partition(".")[2])
        return displayed + "0" * max(0, 2 - fractional)

    def longitude(value: float) -> str:
        return f"{decimal(value)}° {'BT' if value >= 0 else 'BB'}"

    def latitude(value: float) -> str:
        return f"{decimal(value)}° {'LU' if value >= 0 else 'LS'}"

    return f"{longitude(west)} – {longitude(east)}, {latitude(south)} – {latitude(north)}"
