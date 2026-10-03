import importlib.util
import json
from pathlib import Path

import numpy as np

from utils.slab_data import get_slab_depths, load_slab_contours

ROOT_DIR = Path(__file__).resolve().parents[1]
SLAB2_DIR = ROOT_DIR / "data" / "slab2"

_spec = importlib.util.spec_from_file_location(
    "build_slab2_contours", ROOT_DIR / "scripts" / "build_slab2_contours.py"
)
build_slab2_contours = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_slab2_contours)
trace_contours = build_slab2_contours.trace_contours


def _ramp_grid(shape=(6, 8)):
    lat = np.arange(shape[0]) * 0.05
    lon = 100.0 + np.arange(shape[1]) * 0.05
    # Depth increases eastward by 10 km per node: 0, 10, ..., 70 km.
    depth = np.tile(np.arange(shape[1]) * 10.0, (shape[0], 1))
    return lon, lat, depth


def test_trace_contours_follows_straight_isoline():
    lon, lat, depth = _ramp_grid()
    lines = trace_contours(lon, lat, depth, 25.0)
    assert len(lines) == 1
    xs = {round(x, 6) for x, _ in lines[0]}
    assert xs == {100.125}
    assert len(lines[0]) == len(lat)


def test_trace_contours_never_crosses_nodata():
    lon, lat, depth = _ramp_grid()
    depth[:, 4:] = np.nan
    assert trace_contours(lon, lat, depth, 55.0) == []
    for line in trace_contours(lon, lat, depth, 25.0):
        assert all(x <= lon[3] for x, _ in line)


def test_eastern_contours_load_within_their_grid_extent():
    for model in ("sul", "hal", "png", "phi"):
        contours = load_slab_contours(SLAB2_DIR / f"{model}_slab2_dep_contours.in")
        manifest = json.loads((SLAB2_DIR / f"{model}.manifest.json").read_text())
        west, south, east, north = manifest["extent"]
        depths = get_slab_depths(contours)
        assert depths and all(depth % 20 == 0 for depth in depths)
        for feature in contours["features"]:
            for x, y in feature["geometry"]["coordinates"]:
                assert west - 1e-6 <= x <= east + 1e-6
                assert south - 1e-6 <= y <= north + 1e-6


def test_trench_snapshot_includes_north_sulawesi():
    trenches = json.loads(
        (ROOT_DIR / "data" / "indonesia_trench_slab2.geojson").read_text(encoding="utf-8")
    )
    regions = [feature["properties"]["slab_region"] for feature in trenches["features"]]
    assert regions == ["sum", "phi", "png", "sul"]
