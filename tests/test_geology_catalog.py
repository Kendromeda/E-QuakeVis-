from __future__ import annotations

import json
import math
import unittest
import urllib.parse

from utils.geology_catalog import (
    CGMW_2022_SOURCE_URL,
    GEOLOGY_STYLE_CGMW,
    GEOLOGY_STYLE_OFFICIAL,
    GEOLOGY_STYLE_OPTIONS,
    GeologyUnit,
    build_dynamic_layers,
    build_formation_definition,
    build_geology_unit_colors,
    get_cgmw_age_style,
    get_geology_age_style,
    geology_age_legend,
    parse_grouped_query_response,
    query_geology_units_for_province,
    query_geology_units_for_region,
    _esri_rings,
)


class GeologyCatalogTests(unittest.TestCase):
    def test_official_big_ksp_renderer_is_the_default_mode(self) -> None:
        self.assertEqual(
            tuple(GEOLOGY_STYLE_OPTIONS),
            (GEOLOGY_STYLE_OFFICIAL, GEOLOGY_STYLE_CGMW),
        )
        self.assertEqual(
            get_geology_age_style("Holosen").rgb,
            (139, 80, 199),
        )
        self.assertIn(("Kuarter", "#62C232"), geology_age_legend())

        renderer = json.loads(build_dynamic_layers())[0]["drawingInfo"]["renderer"]
        kuarter = next(
            item for item in renderer["uniqueValueInfos"]
            if item["value"] == "Kuarter"
        )
        self.assertEqual(kuarter["symbol"]["color"], [98, 194, 50, 255])
        self.assertEqual(kuarter["symbol"]["outline"]["style"], "esriSLSNull")

    def test_official_cgmw_rgb_values_are_used_verbatim(self) -> None:
        self.assertEqual(get_cgmw_age_style("Holosen").hex, "#FEEBD2")
        self.assertEqual(get_cgmw_age_style("Kuarter").rgb, (249, 249, 127))
        self.assertEqual(get_cgmw_age_style("Miosen").cmyk, (0, 0, 100, 0))
        self.assertEqual(get_cgmw_age_style("Oligocene").hex, "#FDC07A")
        self.assertEqual(get_cgmw_age_style("Trias").rgb, (129, 42, 146))
        self.assertIn("ccgm.org", CGMW_2022_SOURCE_URL)

    def test_mixed_and_legacy_ages_are_explicit_neutral_fallbacks(self) -> None:
        for raw_age in (
            "Tersier",
            "Pra Tersier",
            "Meso - Paleo",
            "Paleo - Meso",
            "Permo Karbon",
            "Pre-Permia",
        ):
            style = get_cgmw_age_style(raw_age)
            self.assertEqual(style.status, "mixed_or_legacy")
            self.assertEqual(style.hex, "#B8B8B8")
            self.assertIn("bukan warna resmi tunggal", style.note)

    def test_grouped_attributes_become_immutable_described_units(self) -> None:
        payload = {
            "features": [{"attributes": {
                "simobj": "Qyt",
                "namobj": "Young Volcanic Products",
                "umurobj": "Kuarter",
                "remark": "Hasil Gunungapi Muda",
                "feature_count": 12,
            }}]
        }
        units = parse_grouped_query_response(payload)

        self.assertEqual(len(units), 1)
        self.assertIsInstance(units[0], GeologyUnit)
        self.assertEqual(units[0].display_name, "Qyt Hasil Gunungapi Muda")
        self.assertIn("atribut resmi Layer 8", units[0].description)
        self.assertEqual(units[0].feature_count, 12)

    def test_definition_is_escaped_and_renderer_keys_on_the_unit(self) -> None:
        unit = GeologyUnit(
            key="safe-key",
            symbol="Tmp",
            name_en="D'Aguilar Formation",
            age="Miosen",
            name_id="Formasi D'Aguilar",
            feature_count=2,
        )
        definition = build_formation_definition([unit], ["safe-key"])

        self.assertIn("simobj = 'Tmp'", definition)
        self.assertIn("Formasi D''Aguilar", definition)
        self.assertIn("D''Aguilar Formation", definition)
        layer = json.loads(build_dynamic_layers(
            [unit], ["safe-key"], style_mode=GEOLOGY_STYLE_CGMW
        ))[0]
        renderer = layer["drawingInfo"]["renderer"]
        # Colouring is per map unit, so the renderer keys on the three fields
        # that identify a unit rather than on its age alone.
        self.assertEqual(renderer["field1"], "simobj")
        self.assertEqual(renderer["field2"], "namobj")
        self.assertEqual(renderer["field3"], "umurobj")
        self.assertEqual(layer["definitionExpression"], definition)
        info = next(
            item for item in renderer["uniqueValueInfos"]
            if item["value"] == "Tmp|D'Aguilar Formation|Miosen"
        )
        self.assertEqual(info["label"], "Tmp Formasi D'Aguilar")
        # A polygon outside the catalog still draws, rather than leaving a hole.
        self.assertIsNotNone(renderer["defaultSymbol"])
        with self.assertRaises(ValueError):
            build_formation_definition([unit], ["unknown-key"])

    def test_units_of_one_age_get_distinct_colors_inside_that_family(self) -> None:
        units = [
            GeologyUnit(
                key=f"k{index}",
                symbol=f"Q{index}",
                name_en=f"Formation {index}",
                age="Kuarter",
                name_id=f"Formasi {index}",
            )
            for index in range(6)
        ]

        colors = build_geology_unit_colors(units)
        values = list(colors.values())

        self.assertEqual(len(colors), 6)
        # Every unit is separable...
        self.assertEqual(len(set(values)), 6)
        # ...and the assignment is stable, so filtering cannot recolour a unit.
        self.assertEqual(colors, build_geology_unit_colors(list(reversed(units))))
        # A single unit in its age keeps the plain age colour.
        solo = build_geology_unit_colors([units[0]])
        self.assertEqual(
            set(solo.values()), {get_geology_age_style("Kuarter").hex}
        )

    def test_dynamic_renderer_can_label_formation_names(self) -> None:
        layer = json.loads(build_dynamic_layers(show_formation_labels=True))[0]

        self.assertTrue(layer["drawingInfo"]["showLabels"])
        self.assertNotIn("showLabels", layer)
        labeling = layer["drawingInfo"]["labelingInfo"][0]
        self.assertEqual(labeling["labelExpression"], "[remark]")
        self.assertEqual(
            labeling["labelPlacement"],
            "esriServerPolygonPlacementAlwaysHorizontal",
        )
        self.assertEqual(labeling["removeDuplicates"], "all")
        self.assertEqual(labeling["multiPart"], "labelLargest")

        symbol_layer = json.loads(build_dynamic_layers(
            show_formation_labels=True,
            formation_label_expression="[simobj]",
            style_mode=GEOLOGY_STYLE_CGMW,
        ))[0]
        self.assertEqual(
            symbol_layer["drawingInfo"]["labelingInfo"][0]["labelExpression"],
            "[simobj]",
        )

    def test_unknown_style_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            get_geology_age_style("Kuarter", "tidak-valid")
        with self.assertRaises(ValueError):
            build_dynamic_layers(style_mode="tidak-valid")

    def test_province_catalog_query_posts_grouped_attributes_without_geometry(self) -> None:
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, _limit):
                return json.dumps({"features": []}).encode("utf-8")

        def opener(request, timeout):
            captured["url"] = request.full_url
            captured["params"] = urllib.parse.parse_qs(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Response()

        province = {
            "features": [{
                "properties": {"PROVINSI": "Jawa Barat"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[106, -8], [109, -8], [109, -5], [106, -8]]],
                },
            }]
        }
        result = query_geology_units_for_province(
            province, "Jawa Barat", opener=opener
        )

        self.assertEqual(result, ())
        self.assertTrue(captured["url"].endswith("/8/query"))
        params = captured["params"]
        self.assertEqual(params["returnGeometry"], ["false"])
        self.assertEqual(
            params["groupByFieldsForStatistics"],
            ["simobj,namobj,umurobj,remark"],
        )
        self.assertIn("feature_count", params["outStatistics"][0])

    def test_region_query_can_scope_the_index_to_selected_districts(self) -> None:
        captured = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, _limit):
                return json.dumps({"features": []}).encode("utf-8")

        def opener(request, timeout):
            captured["params"] = urllib.parse.parse_qs(request.data.decode("utf-8"))
            return Response()

        districts = {
            "features": [{
                "properties": {"WADMKK": "Kota Bandung"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[107.5, -7], [107.8, -7], [107.8, -6.7], [107.5, -7]]],
                },
            }]
        }

        result = query_geology_units_for_region(
            districts,
            ["Kota Bandung"],
            property_name="WADMKK",
            opener=opener,
        )

        self.assertEqual(result, ())
        self.assertEqual(captured["params"]["returnGeometry"], ["false"])
        geometry = json.loads(captured["params"]["geometry"][0])
        self.assertTrue(geometry["rings"])

    def test_closed_admin_ring_is_reduced_and_remains_validly_closed(self) -> None:
        ring = [
            [107 + math.cos(index / 100 * math.tau), -7 + math.sin(index / 100 * math.tau)]
            for index in range(101)
        ]
        ring[-1] = ring[0]
        simplified = _esri_rings(
            {"type": "Polygon", "coordinates": [ring]}, tolerance=0.01
        )[0]

        self.assertLess(len(simplified), len(ring))
        self.assertGreaterEqual(len(simplified), 4)
        self.assertEqual(simplified[0], simplified[-1])

    def test_same_symbol_remark_and_age_stay_distinct_by_english_name(self) -> None:
        first = GeologyUnit(
            "one", "Qp", "Beach Deposit", "Kuarter", "Endapan Pantai", 1
        )
        second = GeologyUnit(
            "two", "Qp", "Coastal Deposit", "Kuarter", "Endapan Pantai", 1
        )

        definition = build_formation_definition([first, second], ["one"])

        self.assertIn("namobj = 'Beach Deposit'", definition)
        self.assertNotIn("Coastal Deposit", definition)


if __name__ == "__main__":
    unittest.main()
