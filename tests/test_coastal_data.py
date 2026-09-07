from __future__ import annotations

from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from utils.coastal_data import (
    coastal_region_names,
    distance_to_coast,
    load_coastal_regions,
)
from utils.data_loader import filter_earthquake_data


ROOT_DIR = Path(__file__).resolve().parents[1]
COASTAL_PATH = ROOT_DIR / "data" / "kabupaten_pesisir.json"

WIDE_DEPTH = (0.0, 1000.0)
WIDE_MAGNITUDE = (0.0, 10.0)
WIDE_DATES = (pd.Timestamp("2000-01-01"), pd.Timestamp("2100-01-01"))


class CoastalDataTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.coastal = load_coastal_regions(COASTAL_PATH)

    def test_seaward_regions_are_coastal_and_inland_ones_are_not(self) -> None:
        for name in ["Kota Padang", "Banyuasin", "Pesisir Selatan", "Kaur"]:
            self.assertIn(name, self.coastal)
        for name in ["Kota Solok", "Kota Palembang", "Lampung Tengah", "Muara Enim"]:
            self.assertNotIn(name, self.coastal)

    def test_coastal_region_names_intersects_selection(self) -> None:
        selection = ["Kota Padang", "Kota Solok", "Lampung Tengah"]
        self.assertEqual(
            coastal_region_names(self.coastal, selection),
            ["Kota Padang"],
        )
        self.assertEqual(coastal_region_names(self.coastal, []), [])

    def test_distance_to_coast_grows_with_offshore_distance(self) -> None:
        # Two points due west of Kota Padang's shore, one farther out to sea.
        df = pd.DataFrame(
            {
                "lintang": [-0.95, -0.95],
                "bujur": [100.30, 99.80],
            }
        )
        distances = distance_to_coast(df, self.coastal, ["Kota Padang"])
        self.assertEqual(len(distances), 2)
        self.assertTrue(np.all(np.isfinite(distances)))
        self.assertLess(distances[0], distances[1])

    def test_distance_is_nan_without_a_coastal_region(self) -> None:
        df = pd.DataFrame({"lintang": [-0.95], "bujur": [100.30]})
        distances = distance_to_coast(df, self.coastal, ["Kota Solok"])
        self.assertTrue(np.all(np.isnan(distances)))

    def test_coast_filter_adds_offshore_events_to_district_selection(self) -> None:
        # A land event in Kota Padang, a near-shore sea event, a far sea event,
        # and a land event just up the coast that belongs to a *different*
        # kabupaten (Padang Pariaman) yet sits within the coast radius.
        df = pd.DataFrame(
            {
                "lintang": [-0.90, -0.95, -2.80, -0.55],
                "bujur": [100.40, 100.20, 99.90, 100.10],
                "kedalaman": [10.0, 15.0, 20.0, 12.0],
                "magnitudo": [4.0, 5.0, 4.5, 4.2],
                "waktu": pd.to_datetime(["2022-01-01"] * 4),
                "kabupaten_kota": [
                    "Kota Padang",
                    "Perairan Sumatera Barat",
                    "Perairan Sumatera Barat",
                    "Padang Pariaman",
                ],
                "provinsi": ["Sumatera Barat"] * 4,
                "jenis_lokasi": ["Darat", "Laut", "Laut", "Darat"],
            }
        )
        distances = pd.Series(
            distance_to_coast(df, self.coastal, ["Kota Padang"]), index=df.index
        )

        near = filter_earthquake_data(
            df,
            WIDE_DEPTH,
            WIDE_MAGNITUDE,
            WIDE_DATES,
            district_names=["Kota Padang"],
            coast_distances=distances,
            max_coast_distance=50.0,
        )
        # Kota Padang's own land event plus the near-shore sea event. The far sea
        # event is out of range; the Padang Pariaman land event is excluded even
        # if within range, because it belongs to another kabupaten.
        self.assertEqual(
            set(near["kabupaten_kota"]), {"Kota Padang", "Perairan Sumatera Barat"}
        )
        self.assertEqual(len(near), 2)
        self.assertNotIn("Padang Pariaman", set(near["kabupaten_kota"]))

        land_only = filter_earthquake_data(
            df,
            WIDE_DEPTH,
            WIDE_MAGNITUDE,
            WIDE_DATES,
            district_names=["Kota Padang"],
        )
        self.assertEqual(set(land_only["kabupaten_kota"]), {"Kota Padang"})


if __name__ == "__main__":
    unittest.main()
