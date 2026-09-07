from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from utils.data_loader import filter_earthquake_data
from utils.map_builder import _volcano_area_bounds, build_indonesia_map
from utils.volcano_data import (
    distance_to_volcanoes,
    filter_volcano_features,
    get_volcano_names,
    load_volcano_geojson,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
VOLCANO_DATA_PATH = ROOT_DIR / "data" / "krb_gunung_api_indonesia_big.geojson"


def _sample_volcanoes() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "NAMA_OBJEK": "Gunung Uji A",
                    "TIPE": "Tipe A",
                    "ELEVASI": "1000 mdpl",
                },
                "geometry": {"type": "Point", "coordinates": [0.0, 0.0]},
            },
            {
                "type": "Feature",
                "properties": {
                    "NAMA_OBJEK": "Gunung Uji B",
                    "TIPE": "Tipe B",
                    "ELEVASI": "800 mdpl",
                },
                "geometry": {"type": "Point", "coordinates": [2.0, 0.0]},
            },
        ],
    }


def _sample_volcano_areas() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "namobj": "UJI A",
                    "display_name": "Gunung Uji A",
                    "indga": 1,
                    "krb_label": "KRB I",
                    "thterbit": 2020,
                    "remark": "Area uji",
                    "volcano_latitude": 0.0,
                    "volcano_longitude": 0.0,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]],
                },
            },
            {
                "type": "Feature",
                "properties": {
                    "namobj": "UJI B",
                    "display_name": "Gunung Uji B",
                    "indga": 2,
                    "krb_label": "KRB II",
                    "thterbit": 2020,
                    "remark": "Area uji",
                    "volcano_latitude": 0.0,
                    "volcano_longitude": 3.0,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[2, -1], [4, -1], [4, 1], [2, 1], [2, -1]]],
                },
            },
        ],
    }


class VolcanoDataTest(unittest.TestCase):
    def test_big_snapshot_loads_all_volcano_hazard_areas(self) -> None:
        volcanoes = load_volcano_geojson(VOLCANO_DATA_PATH)

        self.assertEqual(len(volcanoes["features"]), 596)
        self.assertEqual(len(get_volcano_names(volcanoes)), 109)
        self.assertIn("Gunung Agung", get_volcano_names(volcanoes))
        self.assertTrue(
            all(
                feature["geometry"]["type"] in {"Polygon", "MultiPolygon"}
                for feature in volcanoes["features"]
            )
        )

    def test_distance_uses_nearest_selected_volcano(self) -> None:
        earthquakes = pd.DataFrame(
            {"lintang": [0.0, 0.0], "bujur": [0.0, 1.0]}
        )

        distances = distance_to_volcanoes(
            earthquakes,
            _sample_volcanoes(),
            ["Gunung Uji A"],
        )

        self.assertAlmostEqual(distances[0], 0.0, places=6)
        self.assertAlmostEqual(distances[1], 111.195, places=2)
        self.assertTrue(
            np.isnan(
                distance_to_volcanoes(
                    earthquakes,
                    _sample_volcanoes(),
                    ["Tidak Ada"],
                )
            ).all()
        )

    def test_volcano_distance_participates_in_main_filter(self) -> None:
        earthquakes = pd.DataFrame(
            {
                "kedalaman": [10.0, 10.0, 10.0],
                "magnitudo": [4.0, 4.0, 4.0],
                "waktu": pd.to_datetime(
                    ["2026-01-01", "2026-01-02", "2026-01-03"]
                ),
            }
        )

        filtered = filter_earthquake_data(
            earthquakes,
            depth_range=(0.0, 100.0),
            magnitude_range=(0.0, 10.0),
            date_range=(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-01-31")),
            volcano_distances=pd.Series([5.0, 25.0, 9.0]),
            max_volcano_distance=10.0,
        )

        self.assertEqual(
            filtered["waktu"].dt.day.tolist(),
            [1, 3],
        )

    def test_map_layer_is_off_by_default_and_filters_selected_names(self) -> None:
        hidden_html = build_indonesia_map(
            volcano_geojson=_sample_volcano_areas(),
        ).get_root().render()
        visible_html = build_indonesia_map(
            volcano_geojson=_sample_volcano_areas(),
            show_volcanoes=True,
            volcano_names=["Gunung Uji A"],
        ).get_root().render()

        self.assertNotIn("Kawasan Rawan Bencana Gunung Api (BIG)", hidden_html)
        self.assertIn("Kawasan Rawan Bencana Gunung Api (BIG)", visible_html)
        self.assertIn("Gunung api:", visible_html)
        self.assertIn("Gunung Uji A", visible_html)
        self.assertNotIn("Gunung Uji B", visible_html)
        self.assertIn("KRB I", visible_html)
        self.assertNotIn("<span>Gunung api</span>", visible_html)
        self.assertEqual(
            filter_volcano_features(_sample_volcano_areas(), ["Gunung Uji A"])[
                "features"
            ][0]["properties"]["display_name"],
            "Gunung Uji A",
        )

    def test_selected_volcano_area_bounds_use_actual_polygon(self) -> None:
        bounds = _volcano_area_bounds(
            _sample_volcano_areas(),
            ["Gunung Uji A"],
        )

        self.assertIsNotNone(bounds)
        assert bounds is not None
        self.assertAlmostEqual(bounds[0][0], -1.0, places=6)
        self.assertAlmostEqual(bounds[0][1], -1.0, places=6)
        self.assertAlmostEqual(bounds[1][0], 1.0, places=6)
        self.assertAlmostEqual(bounds[1][1], 1.0, places=6)


if __name__ == "__main__":
    unittest.main()
