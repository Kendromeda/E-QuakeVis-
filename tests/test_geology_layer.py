from __future__ import annotations

import unittest

from utils.geology_layer import (
    GEOLOGY_AGE_LEGEND,
    GEOLOGY_SOURCE_URL,
    build_geology_region_filter,
)
from utils.geology_catalog import GEOLOGY_STYLE_CGMW, GEOLOGY_STYLE_OFFICIAL
from utils.map_builder import (
    GEOLOGY_LAYER_NAME,
    GEOLOGY_MAPSERVER_URL,
    build_indonesia_map,
)


class GeologyLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.province_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"PROVINSI": "Jawa Barat"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[106, -8], [108, -8], [108, -6], [106, -8]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"PROVINSI": "Jawa Tengah"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[109, -8], [111, -8], [111, -6], [109, -8]]],
                    },
                },
            ],
        }
        self.district_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"WADMKK": "Kota Bandung", "WADMPR": "Jawa Barat"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[107.5, -7], [107.8, -7], [107.8, -6.7], [107.5, -7]]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"WADMKK": "Kabupaten Semarang", "WADMPR": "Jawa Tengah"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[110.2, -7.5], [110.7, -7.5], [110.7, -7], [110.2, -7.5]]],
                    },
                },
            ],
        }

    def test_layer_8_can_be_enabled_as_a_visible_sidebar_controlled_overlay(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
            include_geology_layer=True,
            geology_initially_visible=True,
        ).get_root().render()

        self.assertIn(GEOLOGY_LAYER_NAME, rendered_map)
        self.assertIn(GEOLOGY_MAPSERVER_URL, rendered_map)
        self.assertIn('layers: "show:8"', rendered_map)
        self.assertIn('bboxSR: "3857"', rendered_map)
        self.assertIn('imageSR: "3857"', rendered_map)
        self.assertIn("L.CRS.EPSG3857.project", rendered_map)
        self.assertNotIn('layers: "show:9"', rendered_map)
        self.assertIn("geologyLayerInitiallyVisible = true", rendered_map)
        self.assertIn("Resmi BIG/KSP", rendered_map)

    def test_layer_refreshes_by_viewport_and_ignores_stale_requests(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
        ).get_root().render()

        self.assertIn('map.on("moveend resize"', rendered_map)
        self.assertIn('map.on("overlayadd"', rendered_map)
        self.assertIn('map.on("overlayremove"', rendered_map)
        self.assertIn("new AbortController()", rendered_map)
        self.assertIn("requestSequence", rendered_map)
        self.assertIn("exportTimedOut", rendered_map)
        self.assertIn("30000", rendered_map)
        self.assertIn("Layanan geologi melewati batas waktu.", rendered_map)
        self.assertIn("map.getBounds()", rendered_map)
        self.assertIn("map.getSize()", rendered_map)

    def test_export_uses_high_resolution_png_and_skips_duplicate_view_requests(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
        ).get_root().render()

        self.assertIn('format: "png32"', rendered_map)
        self.assertIn('f: "image"', rendered_map)
        self.assertIn("response.blob()", rendered_map)
        self.assertIn("URL.createObjectURL", rendered_map)
        self.assertIn("URL.revokeObjectURL", rendered_map)
        self.assertIn("window.devicePixelRatio", rendered_map)
        self.assertIn("4096 / visibleWidth", rendered_map)
        self.assertIn("4096 / visibleHeight", rendered_map)
        self.assertIn('dpi: "96"', rendered_map)
        self.assertIn("32 * 1024 * 1024", rendered_map)
        self.assertIn("lastExportKey", rendered_map)
        self.assertIn("currentExportKey === lastExportKey", rendered_map)

    def test_legend_separates_age_color_from_formation_identity(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
        ).get_root().render()

        self.assertIn(("Holosen", "#8B50C7"), GEOLOGY_AGE_LEGEND)
        self.assertIn(("Kuarter", "#62C232"), GEOLOGY_AGE_LEGEND)
        self.assertIn(("Miocene / Miosen", "#4AAEC2"), GEOLOGY_AGE_LEGEND)
        self.assertIn("Warna menunjukkan umur batuan", rendered_map)
        self.assertIn("Nama/sandi menunjukkan unit atau formasi", rendered_map)
        self.assertIn("Formasi berbeda dengan umur sama menggunakan warna yang sama", rendered_map)
        self.assertIn("background:#8B50C7", rendered_map)

    def test_cgmw_mode_remains_available_as_an_explicit_option(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
            geology_style_mode=GEOLOGY_STYLE_CGMW,
        ).get_root().render()

        self.assertIn("CGMW 2022", rendered_map)
        self.assertIn("background:#FEEBD2", rendered_map)
        self.assertNotEqual(GEOLOGY_STYLE_OFFICIAL, GEOLOGY_STYLE_CGMW)

    def test_selected_formations_are_sent_to_export_and_identify(self) -> None:
        unit = {
            "key": "Qyt-key",
            "symbol": "Qyt",
            "name_en": "Young Volcanic Products",
            "age": "Kuarter",
            "name_id": "Hasil Gunungapi Muda",
            "feature_count": 5,
        }
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
            geology_units=[unit],
            geology_selected_keys=["Qyt-key"],
        ).get_root().render()

        self.assertIn("dynamicLayers", rendered_map)
        self.assertIn("definitionExpression", rendered_map)
        self.assertIn("simobj = 'Qyt'", rendered_map)
        self.assertIn("Hasil Gunungapi Muda", rendered_map)
        self.assertIn("geologyDefinitionExpression", rendered_map)

    def test_province_selection_filters_export_and_clips_the_overlay(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
            province_geojson=self.province_geojson,
            province_names=["Jawa Barat"],
        ).get_root().render()

        self.assertIn('const geologyFilterLabel = "Provinsi: Jawa Barat"', rendered_map)
        self.assertIn("getVisibleGeologyBounds", rendered_map)
        self.assertIn("createClippedGeologyOverlay", rendered_map)
        self.assertIn("L.svgOverlay", rendered_map)
        self.assertIn('const clipId = "geology-clip-" + requestSequence', rendered_map)
        self.assertNotIn('const clipId = "geology-clip-" + currentSequence', rendered_map)
        self.assertIn("pointInGeologyFilter", rendered_map)
        self.assertIn("106.0", rendered_map)
        self.assertNotIn("111.0", rendered_map)

    def test_district_selection_takes_precedence_over_province_selection(self) -> None:
        region_filter, label = build_geology_region_filter(
            province_geojson=self.province_geojson,
            district_geojson=self.district_geojson,
            province_names=["Jawa Barat"],
            district_names=["Kota Bandung"],
        )

        self.assertEqual(label, "Kabupaten/Kota: Kota Bandung")
        self.assertIsNotNone(region_filter)
        self.assertEqual(len(region_filter["features"]), 1)
        self.assertEqual(
            region_filter["features"][0]["geometry"]["coordinates"][0][0],
            [107.5, -7.0],
        )

    def test_no_region_selection_keeps_the_national_layer(self) -> None:
        region_filter, label = build_geology_region_filter(
            province_geojson=self.province_geojson,
            district_geojson=self.district_geojson,
            province_names=[],
            district_names=[],
        )

        self.assertIsNone(region_filter)
        self.assertEqual(label, "Seluruh Indonesia")

    def test_identify_popup_escapes_remote_attributes_and_cites_source(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
        ).get_root().render()

        self.assertIn("escapeGeologyHtml", rendered_map)
        self.assertIn("textContent = String(value", rendered_map)
        self.assertIn("Pusat Survei Geologi", rendered_map)
        self.assertIn("Badan Geologi", rendered_map)
        self.assertIn("Kebijakan Satu Peta/BIG", rendered_map)
        self.assertTrue(GEOLOGY_SOURCE_URL.endswith("/MapServer/8"))
        self.assertIn('geologyServiceUrl + "/8/query"', rendered_map)
        self.assertIn('method: "POST"', rendered_map)
        self.assertIn('resultRecordCount: "1"', rendered_map)
        self.assertIn('outFields: "fcode,simobj,namobj,umurobj,remark"', rendered_map)
        self.assertIn('popupRow("Kode", attributes.fcode)', rendered_map)
        self.assertIn("identifyTimeoutId", rendered_map)

    def test_layer_can_be_excluded_from_specialized_maps(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
            show_earthquakes=False,
            include_geology_layer=False,
        ).get_root().render()

        self.assertNotIn(GEOLOGY_LAYER_NAME, rendered_map)
        self.assertNotIn(GEOLOGY_MAPSERVER_URL, rendered_map)


if __name__ == "__main__":
    unittest.main()
