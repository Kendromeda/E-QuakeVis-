"""Identify coastal kabupaten/kota and extract their seaward coastline.

Rebuild-time helper. The application reads the committed snapshot
(``data/kabupaten_pesisir.json``), never recomputes it at runtime.

A kabupaten/kota is coastal when part of its administrative boundary faces the
sea. This is decided without any geometry library: each boundary segment is
probed a few kilometres to either side, and a segment counts as coast when one
side lands in the sea (inside no kabupaten polygon) while the other stays on
land. Those seaward segments are the region's coastline; the earthquake
distance-to-coast filter measures against exactly these.

The probe reuses the same point-in-polygon machinery the app already uses to
place earthquakes in a region, so "sea" here means precisely what it means on
the map: a coordinate inside no administrative polygon.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from utils.region_data import (  # noqa: E402
    _build_region_grid,
    _find_region,
    _prepare_regions,
)

DISTRICT_PATH = ROOT_DIR / "data" / "kabupaten_kota_indonesia.geojson"
OUTPUT_PATH = ROOT_DIR / "data" / "kabupaten_pesisir.json"

# How far to step off a boundary segment when asking "is this side sea?".
# Large enough to clear the coarse, generalised outlines, small enough not to
# jump a narrow strait onto a neighbouring island.
PROBE_KM = 3.0
# A lone sea-facing segment can be boundary noise; require a short stretch of
# coast before calling a whole kabupaten coastal.
MIN_COAST_SEGMENTS = 3


def _is_sea(
    longitude: float,
    latitude: float,
    regions: list[dict],
    grid: dict,
) -> bool:
    return _find_region(longitude, latitude, regions, grid) is None


def _coast_segments(
    region: dict,
    regions: list[dict],
    grid: dict,
) -> list[list[list[float]]]:
    """Boundary segments of ``region`` whose seaward side is open water."""
    segments: list[list[list[float]]] = []
    for polygon in region["polygons"]:
        for ring in polygon:
            for start, end in zip(ring, ring[1:]):
                start_lon, start_lat = float(start[0]), float(start[1])
                end_lon, end_lat = float(end[0]), float(end[1])
                mid_lon = (start_lon + end_lon) / 2.0
                mid_lat = (start_lat + end_lat) / 2.0
                delta_lon = end_lon - start_lon
                delta_lat = end_lat - start_lat
                length = math.hypot(delta_lon, delta_lat)
                if length == 0:
                    continue
                # Unit normal to the segment, converted from km to degrees.
                normal_lon = -delta_lat / length
                normal_lat = delta_lon / length
                step_lat = PROBE_KM / 110.574
                step_lon = PROBE_KM / (
                    111.320 * math.cos(math.radians(mid_lat)) or 1.0
                )
                side_a = _is_sea(
                    mid_lon + normal_lon * step_lon,
                    mid_lat + normal_lat * step_lat,
                    regions,
                    grid,
                )
                side_b = _is_sea(
                    mid_lon - normal_lon * step_lon,
                    mid_lat - normal_lat * step_lat,
                    regions,
                    grid,
                )
                if side_a != side_b:
                    segments.append(
                        [[start_lon, start_lat], [end_lon, end_lat]]
                    )
    return segments


def main() -> None:
    geojson = json.loads(DISTRICT_PATH.read_text(encoding="utf-8"))
    regions = _prepare_regions(geojson, "WADMKK", "WADMPR")
    grid = _build_region_grid(regions)

    coastal: dict[str, dict] = {}
    segment_total = 0
    for region in regions:
        name = region.get("name")
        if not name:
            continue
        segments = _coast_segments(region, regions, grid)
        if len(segments) < MIN_COAST_SEGMENTS:
            continue
        coastal[str(name)] = {
            "provinsi": region.get("province"),
            "segmen_pantai": segments,
        }
        segment_total += len(segments)

    OUTPUT_PATH.write_text(
        json.dumps(coastal, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"{len(coastal)} kabupaten/kota pesisir, {segment_total} segmen pantai "
        f"-> {OUTPUT_PATH.name}"
    )


if __name__ == "__main__":
    main()
