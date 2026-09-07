import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.geophysics import (
    GEOPHYSICS_METHODS,
    INTERPOLATION_IDW,
    INTERPOLATION_KRIGING,
    INTERPOLATION_MINIMUM_CURVATURE,
    GeophysicsDataError,
    interpolate_survey_grid,
    read_survey_table,
    symmetric_breaks,
)


class SurveyTableTests(unittest.TestCase):
    def test_common_column_names_are_recognised_without_renaming(self) -> None:
        frame = pd.DataFrame(
            {
                "Longitude": [106.1, 106.2, 106.3],
                "Latitude": [-6.1, -6.2, -6.3],
                "SP (mV)": [-12.5, 3.0, 41.25],
            }
        )

        table = read_survey_table(
            frame, method_name="Self-Potential (SP)", value_column="SP (mV)"
        )

        self.assertEqual(len(table.samples), 3)
        self.assertEqual(table.dropped_rows, 0)
        self.assertEqual(table.method.unit, "mV")
        self.assertEqual(table.samples[0], (106.1, -6.1, -12.5))

    def test_unusable_rows_are_dropped_and_counted_not_silently_kept(self) -> None:
        frame = pd.DataFrame(
            {
                "bujur": [106.1, 106.2, 106.3, 999.0, 106.5],
                "lintang": [-6.1, -6.2, -6.3, -6.4, -6.5],
                "nilai": [1.0, 2.0, "rusak", 4.0, 5.0],
            }
        )

        table = read_survey_table(frame, method_name="Gravity", value_column="nilai")

        # One unparseable value and one out-of-range longitude.
        self.assertEqual(len(table.samples), 3)
        self.assertEqual(table.dropped_rows, 2)

    def test_too_few_usable_points_is_refused_rather_than_mapped(self) -> None:
        frame = pd.DataFrame(
            {"bujur": [106.1, 106.2], "lintang": [-6.1, -6.2], "nilai": [1.0, 2.0]}
        )

        with self.assertRaises(GeophysicsDataError) as raised:
            read_survey_table(frame, method_name="Magnetik", value_column="nilai")

        self.assertIn("minimal", str(raised.exception))

    def test_missing_coordinate_columns_are_reported_clearly(self) -> None:
        frame = pd.DataFrame({"stasiun": ["A", "B", "C"], "nilai": [1.0, 2.0, 3.0]})

        with self.assertRaises(GeophysicsDataError) as raised:
            read_survey_table(frame, method_name="Gravity", value_column="nilai")

        self.assertIn("Kolom koordinat", str(raised.exception))

    def test_bounds_pad_around_the_stations_and_never_collapse(self) -> None:
        frame = pd.DataFrame(
            {
                "bujur": [106.0, 106.0, 106.0],
                "lintang": [-6.0, -6.1, -6.2],
                "nilai": [1.0, 2.0, 3.0],
            }
        )

        table = read_survey_table(frame, method_name="Gravity", value_column="nilai")
        west, south, east, north = table.bounds()

        # A north-south line has zero width; the grid still needs an extent.
        self.assertGreater(east - west, 0.0)
        self.assertLess(south, -6.2)
        self.assertGreater(north, -6.0)


class InterpolationTests(unittest.TestCase):
    BOUNDS = (106.0, -6.4, 106.4, -6.0)

    def _plane_samples(self) -> list[tuple[float, float, float]]:
        # z = 10 + 100*(lon - 106) + 50*(lat + 6.4)
        return [
            (
                106.0 + 0.1 * column,
                -6.4 + 0.1 * row,
                10.0 + 100.0 * (0.1 * column) + 50.0 * (0.1 * row),
            )
            for row in range(5)
            for column in range(5)
        ]

    def test_minimum_curvature_reproduces_a_planar_trend(self) -> None:
        samples = self._plane_samples()

        grid = interpolate_survey_grid(
            samples, self.BOUNDS, 41, 41, INTERPOLATION_MINIMUM_CURVATURE
        )

        # The surface through a plane is that plane: check the centre, away
        # from the boundary rows the stencil cannot reach.
        expected = 10.0 + 100.0 * 0.2 + 50.0 * 0.2
        self.assertAlmostEqual(float(grid[20, 20]), expected, delta=1.0)

    def test_idw_smears_a_planar_trend_where_minimum_curvature_does_not(self) -> None:
        samples = self._plane_samples()

        idw = interpolate_survey_grid(samples, self.BOUNDS, 41, 41, INTERPOLATION_IDW)
        curvature = interpolate_survey_grid(
            samples, self.BOUNDS, 41, 41, INTERPOLATION_MINIMUM_CURVATURE
        )

        expected = 10.0 + 100.0 * 0.2 + 50.0 * 0.2
        # Both are legitimate; they answer different questions, and the map
        # should not present them as interchangeable.
        self.assertLess(
            abs(float(curvature[20, 20]) - expected),
            abs(float(idw[20, 20]) - expected) + 1e-9,
        )

    def test_every_interpolator_returns_the_requested_grid_shape(self) -> None:
        samples = self._plane_samples()

        for method in (
            INTERPOLATION_KRIGING,
            INTERPOLATION_IDW,
            INTERPOLATION_MINIMUM_CURVATURE,
        ):
            with self.subTest(method=method):
                grid = interpolate_survey_grid(samples, self.BOUNDS, 24, 18, method)
                self.assertEqual(grid.shape, (18, 24))
                self.assertTrue(np.isfinite(grid).all())

    def test_idw_returns_the_station_value_at_the_station(self) -> None:
        samples = [
            (106.0, -6.4, -50.0),
            (106.4, -6.4, 0.0),
            (106.0, -6.0, 25.0),
            (106.4, -6.0, 50.0),
        ]

        grid = interpolate_survey_grid(samples, self.BOUNDS, 5, 5, INTERPOLATION_IDW)

        # Corners of the grid coincide with the four stations.
        self.assertAlmostEqual(float(grid[4, 0]), -50.0, places=6)
        self.assertAlmostEqual(float(grid[0, 4]), 50.0, places=6)

    def test_unknown_interpolation_method_is_refused(self) -> None:
        with self.assertRaises(GeophysicsDataError):
            interpolate_survey_grid(
                self._plane_samples(), self.BOUNDS, 10, 10, "spline ajaib"
            )


class LegendBreakTests(unittest.TestCase):
    def test_breaks_stay_centred_on_zero_so_the_sign_survives(self) -> None:
        breaks = symmetric_breaks([-4.0, 1.0, 12.0], classes=4)

        self.assertEqual(len(breaks), 5)
        self.assertAlmostEqual(breaks[0], -12.0)
        self.assertAlmostEqual(breaks[-1], 12.0)
        self.assertAlmostEqual(breaks[2], 0.0)

    def test_all_zero_readings_still_produce_usable_classes(self) -> None:
        breaks = symmetric_breaks([0.0, 0.0, 0.0], classes=2)

        self.assertEqual(len(breaks), 3)
        self.assertLess(breaks[0], breaks[-1])

    def test_methods_cover_the_three_requested_surveys(self) -> None:
        self.assertEqual(
            set(GEOPHYSICS_METHODS),
            {"Self-Potential (SP)", "Gravity", "Magnetik"},
        )
        self.assertTrue(all(m.diverging for m in GEOPHYSICS_METHODS.values()))


if __name__ == "__main__":
    unittest.main()
