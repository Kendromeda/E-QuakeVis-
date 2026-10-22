from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.publication_map import (
    PAGE_SIZES,
    PUBLICATION_HEADER_FILL,
    PUBLICATION_TEMPLATES,
    _bounds_for_representative_scale,
    _draw_scale_bar,
    _format_representative_scale,
    _publication_layout,
    _representative_scale_denominator,
    capture_filtered_map,
)


class PublicationLayoutTests(unittest.TestCase):
    def test_snapshot_forwards_selected_numeric_scale(self) -> None:
        with patch(
            "utils.publication_map.render_publication_map",
            return_value=(b"snapshot", b"pdf"),
        ) as renderer:
            snapshot = capture_filtered_map(
                object(),
                {"type": "FeatureCollection", "features": []},
                {"type": "FeatureCollection", "features": []},
                output_size=(320, 240),
                padding_percent=12,
                marker_scale_percent=100,
                show_basemap=False,
                show_faults=False,
                show_provinces=False,
                representative_scale_denominator=250_000,
            )

        self.assertEqual(snapshot, b"snapshot")
        self.assertEqual(
            renderer.call_args.kwargs["representative_scale_denominator"],
            250_000,
        )

    def test_numeric_scale_uses_output_dpi_and_center_latitude(self) -> None:
        denominator = _representative_scale_denominator(
            (0.0, -1.0, 1.0, 1.0),
            map_width=1113.19458,
            output_dpi=254.0,
        )

        self.assertAlmostEqual(denominator, 1_000_000, delta=10)
        self.assertEqual(
            _format_representative_scale(denominator),
            "SKALA 1 : 1.000.000",
        )
        self.assertEqual(
            _format_representative_scale(123_456),
            "SKALA 1 : 123.456",
        )

    def test_selected_numeric_scale_changes_bounds_without_falsifying_scale(self) -> None:
        map_size = (1000, 600)
        bounds = _bounds_for_representative_scale(
            (106.0, -8.0, 108.0, -6.0),
            map_size,
            denominator=100_000,
            output_dpi=254.0,
        )

        self.assertAlmostEqual(
            _representative_scale_denominator(
                bounds,
                map_width=map_size[0],
                output_dpi=254.0,
            ),
            100_000,
            delta=1,
        )
        self.assertAlmostEqual((bounds[0] + bounds[2]) / 2.0, 107.0, places=6)

        print_bounds = _bounds_for_representative_scale(
            (106.0, -8.0, 108.0, -6.0),
            (2000, 1200),
            denominator=100_000,
            output_dpi=508.0,
        )
        for screen_value, print_value in zip(bounds, print_bounds):
            self.assertAlmostEqual(screen_value, print_value, places=8)

    def test_numeric_scale_is_drawn_above_bar_labels(self) -> None:
        class RecordingDraw:
            def __init__(self) -> None:
                self.text_calls = []
                self.rectangles = []

            def textbbox(self, _position, value, **_kwargs):
                return (0, 0, len(str(value)) * 8, 14)

            def text(self, position, value, **_kwargs):
                self.text_calls.append((position, str(value)))

            def rectangle(self, coordinates, **_kwargs):
                self.rectangles.append(tuple(coordinates))

        draw = RecordingDraw()
        _draw_scale_bar(
            draw,
            (0.0, -1.0, 1.0, 1.0),
            map_width=1113.19458,
            box=(0, 100, 400, 60),
            output_dpi=254.0,
        )

        heading = "SKALA 1 : 1.000.000"
        drawn = {call[1] for call in draw.text_calls}
        self.assertIn(heading, drawn)
        # The bilingual "(SCALE)" suffix was dropped from the heading.
        self.assertNotIn("(SCALE)", drawn)
        self.assertEqual(len(draw.rectangles), 8)
        numeric_y = min(
            call[0][1] for call in draw.text_calls if call[1] == heading
        )
        bar_labels = [call for call in draw.text_calls if call[1] != heading]
        self.assertTrue(bar_labels)
        self.assertLess(numeric_y, min(call[0][1] for call in bar_labels))
        self.assertIn("0", {call[1] for call in bar_labels})

    def test_legacy_bandung_and_rtrw_templates_are_available(self) -> None:
        self.assertIn("A2 Lanskap", PAGE_SIZES)
        self.assertEqual(
            tuple(PUBLICATION_TEMPLATES),
            ("Format Lama", "Peta Geologi Lembar Bandung", "Tata Ruang (RTRW)"),
        )
        self.assertEqual(PUBLICATION_HEADER_FILL, "#ffffff")

        standard_sidebar = _publication_layout(
            "A3 Lanskap", template_name="Format Lama"
        )[4]
        bandung_sidebar = _publication_layout(
            "A3 Lanskap", template_name="Peta Geologi Lembar Bandung"
        )[4]
        self.assertGreater(bandung_sidebar[2], standard_sidebar[2])

    def test_rtrw_sheet_drops_the_title_band_and_grows_the_map(self) -> None:
        _, legacy_header, _, legacy_map, _ = _publication_layout(
            "A3 Lanskap", template_name="Format Lama"
        )
        _, rtrw_header, _, rtrw_map, _ = _publication_layout(
            "A3 Lanskap", template_name="Tata Ruang (RTRW)"
        )

        # The title moves into the sidebar, so the freed band becomes map height.
        self.assertLess(rtrw_header, legacy_header)
        self.assertGreater(rtrw_map[3], legacy_map[3])
        self.assertEqual(rtrw_map[2], legacy_map[2])


if __name__ == "__main__":
    unittest.main()
