"""Loader for the seismic station reference layer.

The stations are stored as a pipe-delimited snapshot in
``data/stasiun_seismik.csv`` with the columns

    Network|Station|Latitude|Longitude|Elevation|SiteName|StartTime|EndTime

Only Station/Latitude/Longitude are needed to draw the layer; the rest is kept
for reference and possible tooltips. Rows without a parseable coordinate, or
whose coordinate falls outside the Indonesian region, are dropped so a stray
typo cannot scatter a triangle across the map.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Generous bounding box around the Indonesian archipelago. Coordinates outside
# it are treated as data-entry errors and skipped.
_MIN_LONGITUDE = 90.0
_MAX_LONGITUDE = 145.0
_MIN_LATITUDE = -12.0
_MAX_LATITUDE = 9.0


def load_seismic_stations(path: str | Path) -> list[tuple[Any, ...]]:
    """Return one record per valid station.

    Each record is ``(latitude, longitude, station, network, site_name,
    elevation, start_time, end_time)`` - the extra fields feed the click popup;
    only the first two are needed to plot the triangle.
    """
    file_path = Path(path)
    if not file_path.exists():
        return []

    records: list[tuple[Any, ...]] = []
    with file_path.open("r", encoding="utf-8", errors="replace") as handle:
        for index, raw_line in enumerate(handle):
            line = raw_line.rstrip("\n").rstrip("\r")
            if not line:
                continue
            columns = line.split("|")
            if len(columns) < 4:
                continue
            station = columns[1].strip()
            # Skip the header row (accepts a leading '#').
            if index == 0 and station.lower() == "station":
                continue
            try:
                latitude = float(columns[2])
                longitude = float(columns[3])
            except (ValueError, TypeError):
                continue
            if not (_MIN_LATITUDE <= latitude <= _MAX_LATITUDE):
                continue
            if not (_MIN_LONGITUDE <= longitude <= _MAX_LONGITUDE):
                continue

            def _column(position: int) -> str:
                return columns[position].strip() if len(columns) > position else ""

            records.append(
                (
                    latitude,
                    longitude,
                    station,
                    columns[0].strip().lstrip("#"),  # network
                    _column(5),  # site name
                    _column(4),  # elevation
                    _column(6),  # start time
                    _column(7),  # end time
                )
            )
    return records


def seismic_station_signature(path: str | Path) -> tuple[str, int]:
    """Return a cheap cache key that changes when the snapshot file changes."""
    file_path = Path(path)
    if not file_path.exists():
        return (str(file_path), 0)
    return (str(file_path), file_path.stat().st_mtime_ns)
