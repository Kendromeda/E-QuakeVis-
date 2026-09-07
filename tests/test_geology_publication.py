from __future__ import annotations

import unittest
from unittest.mock import patch

from utils.publication_map import (
    PAGE_SIZES,
    PUBLICATION_HEADER_FILL,
    PUBLICATION_TEMPLATES,
    _publication_layout,
    _publication_source_lines,
    _draw_scale_bar,
    _geology_legend_pages,
    _resolve_geology_legend_catalog,
    _representative_scale_denominator,
    _bounds_for_representative_scale,
    _format_representative_scale,
    _geology_legend_heading,
    _normalize_geology_units,
    _point_in_rings,
    _polylabel,
    capture_filtered_map,
    draw_geology_vector_layer,
    get_publication_map_size,
    plan_geology_legend_overflow,
)
from utils.geology_catalog import build_geology_unit_colors, geology_render_key
from PIL import Image
from utils.geology_catalog import (
    GEOLOGY_STYLE_CGMW,
    GEOLOGY_STYLE_OFFICIAL,
    GeologyUnit,
)


class GeologyPublicationTests(unittest.TestCase):
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

    def test_unknown_district_catalog_never_falls_back_to_province_units(self) -> None:
        province_unit = GeologyUnit(
            "province", "Qp", "Province Unit", "Kuarter", "Unit Provinsi"
        )

        unknown_units, is_known = _resolve_geology_legend_catalog(
            [province_unit],
            None,
            catalog_known=False,
        )
        verified_empty, empty_is_known = _resolve_geology_legend_catalog(
            [province_unit],
            [],
            catalog_known=True,
        )

        self.assertEqual(unknown_units, ())
        self.assertFalse(is_known)
        self.assertEqual(verified_empty, ())
        self.assertTrue(empty_is_known)

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
        geology_sidebar = _publication_layout(
            "A3 Lanskap", template_name="Peta Geologi Lembar Bandung"
        )[4]
        self.assertGreater(geology_sidebar[2], standard_sidebar[2])

    def test_rtrw_sheet_drops_the_title_band_and_grows_the_map(self) -> None:
        _, legacy_header, _, legacy_map, _, _ = _publication_layout(
            "A3 Lanskap", template_name="Format Lama"
        )
        _, rtrw_header, _, rtrw_map, _, _ = _publication_layout(
            "A3 Lanskap", template_name="Tata Ruang (RTRW)"
        )

        # The title moves into the sidebar, so the freed band becomes map height.
        self.assertLess(rtrw_header, legacy_header)
        self.assertGreater(rtrw_map[3], legacy_map[3])
        self.assertEqual(rtrw_map[2], legacy_map[2])

    def test_geology_sources_are_only_added_when_layer_is_enabled(self) -> None:
        common = dict(
            source_text="E-QuakeVis",
            attribution="Basemap dinonaktifkan",
            show_basemap=False,
            show_faults=False,
            show_trenches=False,
            show_slabs=False,
            show_population_density=False,
            show_provinces=False,
            show_inset=False,
            fault_geojson={"features": []},
            trench_geojson=None,
            slab_geojson=None,
        )
        without_geology = _publication_source_lines(**common, show_geology=False)
        with_geology = _publication_source_lines(
            **common,
            show_geology=True,
            geology_style_mode=GEOLOGY_STYLE_OFFICIAL,
        )
        with_cgmw = _publication_source_lines(
            **common,
            show_geology=True,
            geology_style_mode=GEOLOGY_STYLE_CGMW,
        )

        self.assertFalse(any("Geologi:" in line for line in without_geology))
        self.assertTrue(any("Pusat Survei Geologi" in line for line in with_geology))
        # The per-unit palette is derived here, not published by BIG, so the
        # credit names the source palette without claiming these exact colours.
        self.assertTrue(
            any("palet umur resmi BIG/KSP" in line for line in with_geology)
        )
        self.assertTrue(any("disebar per satuan" in line for line in with_geology))
        self.assertTrue(any("CGMW 2022" in line for line in with_cgmw))
        self.assertEqual(
            _geology_legend_heading(GEOLOGY_STYLE_OFFICIAL),
            "Geologi — Resmi BIG/KSP",
        )
        self.assertEqual(
            _geology_legend_heading(GEOLOGY_STYLE_CGMW),
            "Geologi — CGMW 2022 RGB",
        )

    def test_large_unit_index_paginates_instead_of_truncating(self) -> None:
        units = [
            GeologyUnit(
                key=f"unit-{index}",
                symbol=f"Q{index}",
                name_en=f"Formation {index}",
                age="Kuarter",
                name_id=f"Formasi dengan nama panjang {index}",
                feature_count=1,
            )
            for index in range(90)
        ]
        pages = _geology_legend_pages(
            PAGE_SIZES["A4 Lanskap"], 144.0, units, None
        )

        self.assertGreater(len(pages), 1)
        self.assertTrue(all(page.size == PAGE_SIZES["A4 Lanskap"] for page in pages))
        # Palette pages are written into PDF as uncompressed ASCIIHexDecode,
        # which cost 67 MB per A2/300 DPI page. RGB goes through DCTDecode.
        self.assertTrue(all(page.mode == "RGB" for page in pages))

        age_pages = _geology_legend_pages(
            PAGE_SIZES["A4 Lanskap"], 144.0, [], None
        )
        self.assertEqual(len(age_pages), 1)

        verified_empty_pages = _geology_legend_pages(
            PAGE_SIZES["A4 Lanskap"],
            144.0,
            [],
            None,
            fallback_to_age_legend=False,
        )
        self.assertEqual(verified_empty_pages, [])

    def _vector_feature(
        self, symbol: str, age: str, rings: list[list[list[float]]]
    ) -> dict:
        return {
            "properties": {
                "simobj": symbol,
                "namobj": f"{symbol} Formation",
                "umurobj": age,
                "remark": f"Formasi {symbol}",
            },
            "geometry": {"type": "Polygon", "coordinates": rings},
        }

    @staticmethod
    def _identity(x: float, y: float) -> tuple[float, float]:
        return (x, y)

    def test_small_unit_is_painted_over_the_large_one_around_it(self) -> None:
        big = [[[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]]]
        small = [[[80, 80], [120, 80], [120, 120], [80, 120], [80, 80]]]
        features = [
            self._vector_feature("Qb", "Kuarter", big),
            self._vector_feature("Qs", "Kuarter", small),
        ]
        units = _normalize_geology_units([f["properties"] for f in features])
        colors = build_geology_unit_colors(units)
        canvas = Image.new("RGBA", (200, 200), (0, 0, 0, 0))

        draw_geology_vector_layer(
            canvas, features, self._identity, unit_colors=colors, show_labels=False
        )

        small_hex = colors[geology_render_key(units[1])]
        expected = tuple(int(small_hex[i : i + 2], 16) for i in (1, 3, 5))
        # The enclosed unit survives: painting order is largest area first.
        self.assertEqual(canvas.getpixel((100, 100))[:3], expected)
        big_hex = colors[geology_render_key(units[0])]
        self.assertEqual(
            canvas.getpixel((20, 20))[:3],
            tuple(int(big_hex[i : i + 2], 16) for i in (1, 3, 5)),
        )

    def test_a_hole_stays_transparent_instead_of_burying_what_is_under_it(self) -> None:
        ring_with_hole = [
            [[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]],
            [[80, 80], [120, 80], [120, 120], [80, 120], [80, 80]],
        ]
        features = [self._vector_feature("Qh", "Kuarter", ring_with_hole)]
        units = _normalize_geology_units([f["properties"] for f in features])
        canvas = Image.new("RGBA", (200, 200), (0, 0, 0, 0))

        draw_geology_vector_layer(
            canvas,
            features,
            self._identity,
            unit_colors=build_geology_unit_colors(units),
            show_labels=False,
        )

        self.assertEqual(canvas.getpixel((100, 100))[3], 0)
        self.assertGreater(canvas.getpixel((20, 20))[3], 0)

    def test_label_anchor_lands_inside_a_crescent_where_the_centroid_does_not(self) -> None:
        # A C-shape: its centroid falls in the notch, outside the polygon.
        crescent = [[
            [0, 0], [100, 0], [100, 30], [30, 30], [30, 70],
            [100, 70], [100, 100], [0, 100], [0, 0],
        ]]
        rings = [[list(point) for point in crescent[0]]]
        centroid_x = sum(point[0] for point in crescent[0][:-1]) / 8.0
        centroid_y = sum(point[1] for point in crescent[0][:-1]) / 8.0

        x, y, radius = _polylabel(rings, precision=0.5)

        self.assertFalse(_point_in_rings(centroid_x, centroid_y, rings))
        self.assertTrue(_point_in_rings(x, y, rings))
        self.assertGreater(radius, 0)

    def test_colliding_labels_are_dropped_rather_than_overprinted(self) -> None:
        # A unit enclosed by another: both are roomy enough for a label, but
        # both poles of inaccessibility land on the same spot.
        outer = [[[0, 0], [200, 0], [200, 200], [0, 200], [0, 0]]]
        inner = [[[20, 20], [180, 20], [180, 180], [20, 180], [20, 20]]]
        features = [
            self._vector_feature("Qaaa", "Kuarter", outer),
            self._vector_feature("Qbbb", "Kuarter", inner),
        ]
        units = _normalize_geology_units([f["properties"] for f in features])
        canvas = Image.new("RGBA", (200, 200), (0, 0, 0, 0))

        placed = draw_geology_vector_layer(
            canvas,
            features,
            self._identity,
            unit_colors=build_geology_unit_colors(units),
            label_font_size=20,
        )

        self.assertEqual(placed, 1)

    def test_a_label_too_big_for_its_unit_is_skipped(self) -> None:
        tiny = [[[0, 0], [6, 0], [6, 6], [0, 6], [0, 0]]]
        features = [self._vector_feature("Qverylongsymbol", "Kuarter", tiny)]
        units = _normalize_geology_units([f["properties"] for f in features])
        canvas = Image.new("RGBA", (60, 60), (0, 0, 0, 0))

        placed = draw_geology_vector_layer(
            canvas,
            features,
            self._identity,
            unit_colors=build_geology_unit_colors(units),
            label_font_size=24,
        )

        self.assertEqual(placed, 0)

    def _units(self, count: int) -> list[GeologyUnit]:
        return [
            GeologyUnit(
                key=f"unit-{index}",
                symbol=f"Q{index}",
                name_en=f"Formation {index}",
                age="Kuarter",
                name_id=f"Formasi Contoh {index}",
                feature_count=1,
            )
            for index in range(count)
        ]

    def test_sidebar_sized_catalog_needs_no_block_beside_the_map(self) -> None:
        plan = plan_geology_legend_overflow(
            "A2 Lanskap", 144.0, "Format Lama", unit_count=6
        )

        self.assertFalse(plan.is_active)
        self.assertEqual(plan.width, 0)
        self.assertEqual(
            get_publication_map_size("A2 Lanskap", 144.0, "Format Lama"),
            get_publication_map_size(
                "A2 Lanskap", 144.0, "Format Lama", geology_unit_count=6
            ),
        )

    def test_overflow_block_takes_its_width_from_the_map_never_overlapping(self) -> None:
        labels = [unit.display_name for unit in self._units(64)]
        plan = plan_geology_legend_overflow(
            "A2 Lanskap",
            144.0,
            "Format Lama",
            unit_count=64,
            unit_labels=labels,
        )

        self.assertTrue(plan.is_active)
        self.assertEqual(plan.overflow_count, 58)
        self.assertGreaterEqual(plan.capacity, plan.overflow_count)
        self.assertEqual(plan.remainder, 0)

        _, _, _, map_box, sidebar_box, legend_box = _publication_layout(
            "A2 Lanskap", 144.0, "Format Lama", plan.width
        )
        self.assertIsNotNone(legend_box)
        assert legend_box is not None
        # The block sits on the left edge, before the map.
        self.assertLessEqual(legend_box[0] + legend_box[2], map_box[0])
        self.assertLessEqual(map_box[0] + map_box[2], sidebar_box[0])
        # The block spans the same band as the map rather than sitting below it.
        self.assertEqual(legend_box[1], sidebar_box[1])
        self.assertEqual(legend_box[3], sidebar_box[3])

        wide = get_publication_map_size("A2 Lanskap", 144.0, "Format Lama")
        narrowed = get_publication_map_size(
            "A2 Lanskap",
            144.0,
            "Format Lama",
            geology_unit_count=64,
            geology_unit_labels=labels,
        )
        self.assertLess(narrowed[0], wide[0])
        self.assertEqual(narrowed[1], wide[1])

    def test_block_prefers_one_narrow_column_over_a_wide_multi_column_one(self) -> None:
        single = plan_geology_legend_overflow(
            "A2 Lanskap", 144.0, "Format Lama", unit_count=64
        )
        # Far more units than one column can hold at any readable size.
        many = plan_geology_legend_overflow(
            "A2 Lanskap", 144.0, "Format Lama", unit_count=400
        )

        self.assertEqual(single.column_count, 1)
        self.assertGreater(many.column_count, 1)
        self.assertGreater(many.width, single.width)

    def test_index_uses_only_units_passed_from_the_effective_region(self) -> None:
        district_units = [
            GeologyUnit(
                key=f"district-{index}",
                symbol=f"Q{index}",
                name_en=f"Unit {index}",
                age="Kuarter",
                name_id=f"Formasi {index}",
            )
            for index in range(5)
        ]
        pages = _geology_legend_pages(
            PAGE_SIZES["A4 Lanskap"], 144.0, district_units, None
        )

        self.assertEqual(len(pages), 1)


if __name__ == "__main__":
    unittest.main()
