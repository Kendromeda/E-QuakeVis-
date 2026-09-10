from __future__ import annotations

import math

import pandas as pd
import pytest

from utils.hypocenter_3d import (
    DEPTH_STOPS,
    MAX_DEPTH_KM,
    build_boundary_paths,
    build_fault_paths,
    build_hypocenters,
    build_scene,
    build_slab_paths,
    build_trench_paths,
    build_volcano_points,
    depth_color,
    depth_legend_stops,
    magnitude_radius,
    project_km,
    scene_center,
)


def sample_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "waktu": pd.to_datetime(
                ["2025-01-02 13:59:50", "2025-01-03 04:10:00", "2025-01-04 09:00:00"]
            ),
            "lintang": [-6.71, -7.20, -8.10],
            "bujur": [106.70, 107.40, 110.20],
            "kedalaman": [13.0, 180.0, 620.0],
            "magnitudo": [2.0, 5.4, 4.1],
            "provinsi": ["Jawa Barat", "Jawa Barat", "Jawa Tengah"],
        }
    )


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[index : index + 2], 16) for index in (1, 3, 5))


def test_depth_color_follows_the_ramp_stops():
    for depth, rgb in DEPTH_STOPS:
        assert hex_to_rgb(depth_color(depth)) == rgb


def test_depth_color_interpolates_between_stops():
    mid = hex_to_rgb(depth_color(15))
    for index in range(3):
        low, high = DEPTH_STOPS[0][1][index], DEPTH_STOPS[1][1][index]
        assert mid[index] == pytest.approx(round(low + (high - low) * 0.5), abs=1)


def test_depth_color_clamps_out_of_range_and_missing_values():
    assert hex_to_rgb(depth_color(-50)) == DEPTH_STOPS[0][1]
    assert hex_to_rgb(depth_color(MAX_DEPTH_KM + 500)) == DEPTH_STOPS[-1][1]
    assert hex_to_rgb(depth_color(float("nan"))) == DEPTH_STOPS[0][1]
    assert hex_to_rgb(depth_color(None)) == DEPTH_STOPS[0][1]


def test_magnitude_radius_grows_with_magnitude():
    assert magnitude_radius(6) > magnitude_radius(4) > magnitude_radius(2)
    assert magnitude_radius(float("nan")) > 0
    assert magnitude_radius(None) > 0


def test_project_km_is_zero_at_the_centre_and_signed_around_it():
    assert project_km(110.0, -5.0, 110.0, -5.0) == (0.0, 0.0)
    east, north = project_km(111.0, -4.0, 110.0, -5.0)
    assert east > 0 and north > 0
    west, south = project_km(109.0, -6.0, 110.0, -5.0)
    assert west < 0 and south < 0


def test_project_km_uses_real_kilometres():
    # One degree of latitude is about 110 km anywhere.
    _, north = project_km(110.0, -4.0, 110.0, -5.0)
    assert north == pytest.approx(110.6, abs=0.5)
    # One degree of longitude shrinks with the cosine of latitude.
    east, _ = project_km(111.0, -60.0, 110.0, -60.0)
    assert east == pytest.approx(111.32 * math.cos(math.radians(-60)), abs=0.5)


def test_build_hypocenters_keeps_depth_in_km_and_labels_events():
    frame = build_hypocenters(sample_frame())
    assert len(frame) == 3
    assert list(frame["depth_km"]) == [13.0, 180.0, 620.0]
    assert frame.loc[0, "color"] == depth_color(13.0)
    assert frame.loc[1, "radius"] > frame.loc[0, "radius"]
    assert frame.loc[1, "label"].startswith("M 5.4 · 180 km · Jawa Barat")


def test_build_hypocenters_drops_rows_without_a_position():
    frame = sample_frame()
    frame.loc[0, "bujur"] = None
    frame.loc[1, "kedalaman"] = float("nan")
    result = build_hypocenters(frame)
    assert len(result) == 1
    assert result.loc[0, "lon"] == 110.20


def test_build_hypocenters_handles_empty_and_missing_columns():
    empty = build_hypocenters(pd.DataFrame())
    assert empty.empty
    assert "depth_km" in empty.columns

    minimal = pd.DataFrame(
        {"lintang": [-6.0], "bujur": [106.0], "kedalaman": [10.0], "magnitudo": [3.0]}
    )
    result = build_hypocenters(minimal)
    assert len(result) == 1
    assert "M 3.0" in result.loc[0, "label"]


def test_build_fault_paths_reads_both_line_geometries():
    geojson = {
        "features": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[106.0, -6.0], [106.5, -6.4]],
                },
                "properties": {"nama": "Cimandiri"},
            },
            {
                "geometry": {
                    "type": "MultiLineString",
                    "coordinates": [
                        [[107.0, -7.0], [107.4, -7.2]],
                        [[108.0, -7.5], [108.3, -7.7]],
                    ],
                },
                "properties": {"Name": "Baribis"},
            },
        ]
    }
    paths = build_fault_paths(geojson)
    assert len(paths) == 3
    assert paths[0]["name"] == "Cimandiri"
    assert all(point[2] == 0.0 for point in paths[0]["path"])

    filtered = build_fault_paths(geojson, feature_indexes=[1])
    assert len(filtered) == 2
    assert {path["name"] for path in filtered} == {"Baribis"}


def test_build_slab_paths_carry_contour_depth_and_matching_colour():
    geojson = {
        "features": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[122.9, -5.1], [122.96, -5.15]],
                },
                "properties": {"depth": 200.0},
            },
            {
                "geometry": {"type": "LineString", "coordinates": [[1, 1], [2, 2]]},
                "properties": {"depth": "bukan angka"},
            },
        ]
    }
    paths = build_slab_paths(geojson)
    assert len(paths) == 1
    assert paths[0]["depth_km"] == 200.0
    assert all(point[2] == 200.0 for point in paths[0]["path"])
    assert paths[0]["color"] == depth_color(200.0)
    assert "200 km" in paths[0]["name"]


def test_build_trench_paths_sit_on_the_surface():
    geojson = {
        "features": [
            {
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[100.0, -3.0], [101.0, -4.0]],
                },
                "properties": {"Name": "Sunda Trench"},
            }
        ]
    }
    paths = build_trench_paths(geojson)
    assert paths[0]["name"] == "Sunda Trench"
    assert all(point[2] == 0.0 for point in paths[0]["path"])


def test_build_volcano_points_uses_the_shared_reader():
    geojson = {
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [107.6, -6.77]},
                "properties": {"NAMA_OBJEK": "Tangkuban Perahu"},
            }
        ]
    }
    points = build_volcano_points(geojson)
    assert points == [{"lon": 107.6, "lat": -6.77, "name": "Tangkuban Perahu"}]


def test_build_boundary_paths_thins_rings_but_keeps_the_last_vertex():
    ring = [[float(index), 0.0] for index in range(10)]
    geojson = {"features": [{"geometry": {"type": "Polygon", "coordinates": [ring]}}]}
    dense = build_boundary_paths(geojson, step=1)
    thinned = build_boundary_paths(geojson, step=3)
    assert len(dense[0]["path"]) == 10
    assert len(thinned[0]["path"]) < 10
    assert thinned[0]["path"][-1][0] == 9.0


def test_build_boundary_paths_handles_multipolygon():
    geojson = {
        "features": [
            {
                "geometry": {
                    "type": "MultiPolygon",
                    "coordinates": [
                        [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]]],
                        [[[5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 5.0]]],
                    ],
                }
            }
        ]
    }
    assert len(build_boundary_paths(geojson, step=1)) == 2


def test_scene_center_falls_back_when_there_is_nothing_to_show():
    assert scene_center(build_hypocenters(pd.DataFrame())) == (118.0, -2.5)
    longitude, latitude = scene_center(build_hypocenters(sample_frame()))
    assert 106.6 < longitude < 110.3
    assert -8.2 < latitude < -6.7


def test_build_scene_projects_every_layer_into_the_same_frame():
    scene = build_scene(
        sample_frame(),
        fault_geojson={
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[106.0, -6.0], [106.5, -6.4]],
                    },
                    "properties": {"nama": "Cimandiri"},
                }
            ]
        },
        slab_geojson={
            "features": [
                {
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[108.0, -9.0], [109.0, -9.2]],
                    },
                    "properties": {"depth": 100.0},
                }
            ]
        },
        volcano_geojson={
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [107.6, -6.77]},
                    "properties": {"NAMA_OBJEK": "Tangkuban Perahu"},
                }
            ]
        },
        stem_min_magnitude=5.0,
    )

    assert len(scene["points"]["x"]) == 3
    assert scene["points"]["s"] == [False, True, False]
    assert scene["points"]["d"] == [13.0, 180.0, 620.0]

    # The centre of the projection is the origin of the scene.
    centre_longitude, centre_latitude = scene["center"]
    assert sum(scene["points"]["x"]) == pytest.approx(0.0, abs=1.0)

    assert len(scene["faults"]) == 1
    assert len(scene["slabs"]) == 1
    assert all(point[2] == 100.0 for point in scene["slabs"][0]["p"])
    assert scene["slabs"][0]["c"] == depth_color(100.0)

    assert len(scene["volcanoes"]) == 1
    volcano = scene["volcanoes"][0]
    expected_east, expected_north = project_km(
        107.6, -6.77, centre_longitude, centre_latitude
    )
    assert volcano["x"] == pytest.approx(expected_east, abs=0.2)
    assert volcano["y"] == pytest.approx(expected_north, abs=0.2)


def test_build_scene_survives_empty_input_and_missing_layers():
    scene = build_scene(pd.DataFrame())
    assert scene["points"]["x"] == []
    assert scene["faults"] == []
    assert scene["slabs"] == []
    assert scene["volcanoes"] == []
    assert scene["center"] == [118.0, -2.5]


def test_depth_legend_stops_match_the_ramp():
    stops = depth_legend_stops()
    assert len(stops) == len(DEPTH_STOPS)
    assert stops[0][0] == 0.0
    assert stops[-1][0] == MAX_DEPTH_KM
    assert stops[0][1] == depth_color(0.0)
