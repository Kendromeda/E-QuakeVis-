from __future__ import annotations

import base64
from datetime import time
from pathlib import Path
from tempfile import TemporaryDirectory
from io import BytesIO
import math
import urllib.error
import unittest
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFont

from utils.data_loader import (
    apply_column_mapping,
    DataValidationError,
    append_to_master_database,
    clean_earthquake_data,
    deduplicate_database_records,
    detect_header_row,
    filter_earthquake_data,
    has_canonical_columns,
    inspect_uploaded_file,
    load_master_database,
    load_uploaded_file,
    normalize_for_database,
    save_master_database,
    suggest_column_mapping,
    validate_required_columns,
)
from utils.fault_data import (
    build_fault_province_index,
    distance_to_fault_segments,
    distance_to_faults,
    enrich_earthquakes_with_faults,
    get_fault_names,
    get_fault_segment_options,
    load_fault_geojson,
)
from utils.earthquake_store import (
    default_query_dates,
    ensure_working_database,
    get_store_summary,
    query_earthquakes,
    source_signature,
    store_is_current,
)
from utils.map_builder import (
    BASEMAP_CYCLOSM,
    BASEMAP_HUMANITARIAN,
    BASEMAP_OPENTOPO,
    BASEMAP_RELIEF,
    BASEMAP_SHORTBREAD,
    POPULATION_DENSITY_COLORS,
    build_earthquake_feature_group,
    build_indonesia_map,
    classify_fault_type,
    _fault_symbol_canvas_records,
    filter_hidden_earthquakes,
    get_depth_style,
    get_earthquake_event_id,
    get_magnitude_marker,
    get_scaled_magnitude_radius,
    get_fault_dip_direction,
    get_fault_label_records,
    get_fault_symbol_side,
    get_strike_slip_sense,
)
from utils.publication_map import (
    MAX_PUBLICATION_TILE_COUNT,
    OUTPUT_DPI,
    PAGE_SIZES,
    PUBLICATION_DPI_OPTIONS,
    PublicationMapError,
    TILE_DOWNLOAD_TIMEOUT_SECONDS,
    TileRetryAfterError,
    _TILE_RETRY_NOT_BEFORE,
    _choose_zoom,
    _decode_raster_tile,
    _download_raster_tile,
    _draw_coordinate_grid,
    _draw_locator_inset,
    _draw_north_arrow,
    _draw_scale_bar,
    _draw_trench_polyline,
    _fit_bounds_to_web_mercator_aspect,
    _fetch_tile_from_templates,
    _format_coordinate_label,
    _logical_raster_cache_path,
    _mercator_pixel,
    _overview_bounds,
    _publication_layout,
    _publication_magnitude_radius,
    _publication_source_lines,
    _publication_tile_limit,
    _render_basemap,
    _render_overview_map,
    _render_raster_basemap,
    get_publication_page_size,
    _valid_tile_bytes,
    _write_cached_tile,
    _should_compact_sidebar_legends,
    capture_filtered_map,
    publication_filename_stem,
    render_publication_map,
)
from utils.region_data import enrich_earthquakes_with_regions, load_region_geojson
from utils.slab_data import (
    SlabDataError,
    filter_slab_contours,
    get_slab_depths,
    load_slab_contours,
)
from utils.seismic_station import load_seismic_stations
from utils.stats import calculate_summary_stats
from utils.trench_data import load_trench_geojson, load_trench_shapefile


ROOT_DIR = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT_DIR / "sample_data"
FAULT_DATA_PATH = ROOT_DIR / "data" / "sesar_pusgen_2024.geojson"
PROVINCE_DATA_PATH = ROOT_DIR / "data" / "provinsi_indonesia_38.geojson"
DISTRICT_DATA_PATH = ROOT_DIR / "data" / "kabupaten_kota_indonesia.geojson"
TRENCH_DATA_PATH = ROOT_DIR / "Trench" / "Sumatra_Java_trench.shp"
TRENCH_GEOJSON_PATH = ROOT_DIR / "data" / "indonesia_trench_slab2.geojson"
SLAB_DATA_PATH = ROOT_DIR / "data" / "sum_slab2_dep_contours.in"
STATION_DATA_PATH = ROOT_DIR / "data" / "stasiun_seismik.csv"


class DataPipelineTest(unittest.TestCase):
    def test_publication_trench_teeth_match_interactive_orientation(self) -> None:
        draw = Mock()

        _draw_trench_polyline(draw, [(10.0, 30.0), (110.0, 30.0)], 1.0)

        self.assertGreater(draw.polygon.call_count, 0)
        for polygon_call in draw.polygon.call_args_list:
            base_left, base_right, tip = polygon_call.args[0]
            self.assertLess(tip[1], base_left[1])
            self.assertLess(tip[1], base_right[1])

    def test_canonical_upload_schema_needs_no_column_mapping(self) -> None:
        csv_upload = BytesIO(
            b"tanggal,jam,lintang,bujur,kedalaman,magnitudo\n"
            b"2025-01-01,01:02:03,-6.8,107.6,10,3.2\n"
        )
        csv_upload.name = "gempa.csv"

        inspection = inspect_uploaded_file(csv_upload)
        loaded = load_uploaded_file(csv_upload)

        self.assertTrue(inspection["canonical"])
        self.assertTrue(has_canonical_columns(loaded.columns))
        self.assertEqual(len(loaded), 1)

    def test_windows_encoded_master_is_loaded_and_migrated_to_utf8(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            master_path = Path(temporary_directory) / "gempa_master.csv"
            legacy_csv = (
                "tanggal,jam,lintang,bujur,kedalaman,magnitudo,jenis_lokasi,"
                "keterangan_lokasi,dirasakan\n"
                "2025-01-01,01:02:03,-6.8,107.6,10,3.2,Darat,"
                "10 km\u00a0Barat Bandung,\n"
            )
            master_path.write_bytes(legacy_csv.encode("cp1252"))

            loaded = load_master_database(master_path)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(
                loaded.loc[0, "keterangan_lokasi"],
                "10 km\u00a0Barat Bandung",
            )
            persisted = master_path.read_bytes().decode("utf-8")
            self.assertIn("10 km\u00a0Barat Bandung", persisted)
            self.assertNotIn("jenis_lokasi", persisted.splitlines()[0])

    def test_date_order_is_inferred_from_unambiguous_values(self) -> None:
        base_columns = {
            "jam": ["01:02:03", "04:05:06"],
            "lintang": [-6.8, -6.9],
            "bujur": [107.6, 107.7],
            "kedalaman": [10, 11],
            "magnitudo": [3.2, 3.3],
        }
        month_first = normalize_for_database(
            pd.DataFrame({"tanggal": ["1/2/2025", "2/14/2025"], **base_columns})
        )
        day_first = normalize_for_database(
            pd.DataFrame({"tanggal": ["1/2/2025", "14/2/2025"], **base_columns})
        )

        self.assertEqual(month_first["tanggal"].tolist(), ["2025-01-02", "2025-02-14"])
        self.assertEqual(day_first["tanggal"].tolist(), ["2025-02-01", "2025-02-14"])

    def test_sqlite_store_precomputes_regions_faults_and_queries_dates(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "earthquakes.sqlite"
            archive_paths = [SAMPLE_DIR / "gempa_sample_valid.csv"]
            reference_paths = [
                FAULT_DATA_PATH,
                PROVINCE_DATA_PATH,
                DISTRICT_DATA_PATH,
            ]
            rebuilt = ensure_working_database(
                database_path,
                archive_paths,
                reference_paths,
                load_fault_geojson(FAULT_DATA_PATH),
                load_region_geojson(PROVINCE_DATA_PATH),
                load_region_geojson(DISTRICT_DATA_PATH),
            )
            summary = get_store_summary(database_path)
            query_dates = default_query_dates(summary, days=90)
            queried = query_earthquakes(database_path, *query_dates)

            self.assertTrue(rebuilt)
            self.assertEqual(len(queried), 6)
            self.assertEqual(int(summary["row_count"]), 6)
            self.assertTrue(
                {
                    "provinsi",
                    "kabupaten_kota",
                    "jenis_lokasi",
                    "jarak_sesar_km",
                }.issubset(queried.columns)
            )
            self.assertFalse(queried["provinsi"].isna().any())
            self.assertFalse(queried["jarak_sesar_km"].isna().any())
            self.assertTrue(
                store_is_current(
                    database_path,
                    source_signature(archive_paths, reference_paths),
                )
            )

    def test_upload_mapping_suggests_prs_style_columns(self) -> None:
        source = pd.DataFrame(
            {
                "Tanggal (WIB)": [pd.Timestamp("2025-01-02")],
                "Waktu (WIB)": [time(3, 4, 5)],
                "Episenter / Lintang": [-6.8],
                "Episenter / Bujur": [107.6],
                "Kedalaman (km)": [10],
                "Magnitudo": [3.2],
                "Keterangan / PG di": ["Darat"],
                "Keterangan / Lokasi": ["Bandung"],
                "Keterangan / Dirasakan": ["II MMI"],
            }
        )

        mapping = suggest_column_mapping(source.columns)
        mapped = apply_column_mapping(source, mapping)
        normalized = normalize_for_database(mapped)

        self.assertEqual(mapping["tanggal"], "Tanggal (WIB)")
        self.assertEqual(mapping["lintang"], "Episenter / Lintang")
        self.assertNotIn("jenis_lokasi", mapping)
        self.assertNotIn("jenis_lokasi", mapped.columns)
        self.assertEqual(normalized.loc[0, "tanggal"], "2025-01-02")
        self.assertEqual(normalized.loc[0, "jam"], "03:04:05")
        self.assertEqual(normalized.loc[0, "keterangan_lokasi"], "Bandung")

    def test_multilevel_prs_header_row_is_detected(self) -> None:
        raw = pd.DataFrame(
            [
                ["PARAMETER TERPILIH", None, None, None, None, None, None],
                [None, None, None, None, None, None, None],
                ["Tanggal (WIB)", "Waktu (WIB)", "Episenter", None, "Kedalaman (km)", "Magnitudo", "Keterangan"],
                [None, None, "Lintang", "Bujur", None, None, "PG di"],
                [pd.Timestamp("2025-01-02"), time(3, 4, 5), -6.8, 107.6, 10, 3.2, "Darat"],
            ]
        )

        self.assertEqual(detect_header_row(raw), 2)

    def test_column_mapping_requires_every_database_field(self) -> None:
        source = pd.DataFrame({"Tanggal": ["2025-01-01"]})

        with self.assertRaises(DataValidationError):
            apply_column_mapping(source, {"tanggal": "Tanggal"})

    def test_faults_are_indexed_by_land_and_nearby_offshore_province(self) -> None:
        province_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {"PROVINSI": "Provinsi A"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[100, -1], [101, -1], [101, 1], [100, 1], [100, -1]]],
                    },
                },
                {
                    "properties": {"PROVINSI": "Provinsi B"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[110, -1], [111, -1], [111, 1], [110, 1], [110, -1]]],
                    },
                },
            ],
        }
        fault_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {"Name": "Sesar Darat A"},
                    "geometry": {"type": "LineString", "coordinates": [[100.2, 0], [100.8, 0]]},
                },
                {
                    "properties": {"Name": "Sesar Laut A"},
                    "geometry": {"type": "LineString", "coordinates": [[102, 0], [102.5, 0]]},
                },
                {
                    "properties": {"Name": "Sesar Darat B"},
                    "geometry": {"type": "LineString", "coordinates": [[110.2, 0], [110.8, 0]]},
                },
            ],
        }

        index = build_fault_province_index(fault_geojson, province_geojson)

        self.assertEqual(index["Provinsi A"], [0, 1])
        self.assertEqual(index["Provinsi B"], [2])

    def test_fault_feature_filter_does_not_expand_duplicate_names(self) -> None:
        properties = {
            "Name": "Nama Sama",
            "Region": "Uji",
            "Type": "SS",
            "Mmax": 6.0,
            "Sliprate_m": "1",
        }
        fault_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "properties": {**properties, "Segment": "Segmen Barat"},
                    "geometry": {"type": "LineString", "coordinates": [[100, 0], [101, 0]]},
                },
                {
                    "properties": {**properties, "Segment": "Segmen Timur"},
                    "geometry": {"type": "LineString", "coordinates": [[110, 0], [111, 0]]},
                },
            ],
        }

        rendered_map = build_indonesia_map(
            fault_geojson=fault_geojson,
            fault_feature_indexes=[0],
            show_basemap=False,
        ).get_root().render()

        self.assertIn("Segmen Barat", rendered_map)
        self.assertNotIn("Segmen Timur", rendered_map)

    def test_empty_fault_selection_hides_all_fault_features(self) -> None:
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        fault_name = fault_geojson["features"][0]["properties"]["Name"]

        all_faults = build_indonesia_map(
            fault_geojson=fault_geojson,
            fault_names=None,
            show_basemap=False,
        ).get_root().render()
        no_faults = build_indonesia_map(
            fault_geojson=fault_geojson,
            fault_names=[],
            show_basemap=False,
        ).get_root().render()

        self.assertIn(fault_name, all_faults)
        self.assertNotIn(fault_name, no_faults)

    def test_fault_codes_map_to_cartographic_classes(self) -> None:
        self.assertEqual(classify_fault_type("SS-LL90"), "Sesar Geser")
        self.assertEqual(classify_fault_type("R45S"), "Sesar Naik")
        self.assertEqual(classify_fault_type("RR45S"), "Sesar Naik")
        self.assertEqual(classify_fault_type("N60W"), "Sesar Normal")

    def test_fault_symbols_follow_pusgen_direction_and_slip_sense(self) -> None:
        self.assertEqual(get_fault_dip_direction("R45S"), "S")
        self.assertEqual(get_fault_dip_direction("N60NW"), "NW")
        self.assertEqual(get_fault_symbol_side("R45S", 1.0, 0.0), 1)
        self.assertEqual(get_fault_symbol_side("R45N", 1.0, 0.0), -1)
        self.assertEqual(get_fault_symbol_side("R45", 1.0, 0.0), -1)
        self.assertEqual(get_strike_slip_sense("SS-LL90"), -1)
        self.assertEqual(get_strike_slip_sense("SS-RL90"), 1)

    def test_fault_labels_are_generated_once_per_fault_name(self) -> None:
        faults = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar Uji", "Type": "R45S"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[106.0, -7.0], [107.0, -7.0]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar Uji", "Type": "R45S"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[107.0, -7.0], [107.2, -7.1]],
                    },
                },
            ],
        }
        self.assertEqual(
            get_fault_label_records(faults),
            [("Sesar Uji", -7.0, 106.5)],
        )

    def test_trench_shapefile_loads_as_wgs84_lines(self) -> None:
        trench_geojson = load_trench_shapefile(TRENCH_DATA_PATH)

        self.assertEqual(trench_geojson["type"], "FeatureCollection")
        self.assertEqual(len(trench_geojson["features"]), 2)
        self.assertTrue(
            all(
                feature["geometry"]["type"] == "LineString"
                for feature in trench_geojson["features"]
            )
        )

    def test_trench_geojson_loads_slab2_indonesia_lines(self) -> None:
        trench_geojson = load_trench_geojson(TRENCH_GEOJSON_PATH)

        self.assertEqual(trench_geojson["type"], "FeatureCollection")
        names = [f["properties"]["Name"] for f in trench_geojson["features"]]
        # Sunda-Banda plus the Philippine and New Guinea trenches.
        self.assertGreaterEqual(len(trench_geojson["features"]), 3)
        self.assertTrue(any("Sunda" in name for name in names))
        self.assertTrue(any("Filipina" in name for name in names))
        self.assertTrue(any("Papua" in name for name in names))

        for feature in trench_geojson["features"]:
            self.assertEqual(feature["geometry"]["type"], "LineString")
            self.assertGreaterEqual(len(feature["geometry"]["coordinates"]), 2)
            self.assertIn("Slab2", feature["properties"]["source"])
            # Every vertex sits within the Indonesian archipelago window.
            for longitude, latitude in feature["geometry"]["coordinates"]:
                self.assertTrue(90.0 <= longitude <= 142.0)
                self.assertTrue(-12.0 <= latitude <= 14.5)

        sunda = next(f for f in trench_geojson["features"] if "Sunda" in f["properties"]["Name"])
        self.assertGreater(len(sunda["geometry"]["coordinates"]), 300)

    def test_map_renders_trench_and_fault_type_legend(self) -> None:
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        trench_geojson = load_trench_geojson(TRENCH_GEOJSON_PATH)
        rendered_map = build_indonesia_map(
            fault_geojson=fault_geojson,
            trench_geojson=trench_geojson,
            show_basemap=False,
        ).get_root().render()
        hidden_map = build_indonesia_map(
            fault_geojson=fault_geojson,
            trench_geojson=trench_geojson,
            show_trenches=False,
            show_basemap=False,
        ).get_root().render()

        self.assertIn("Palung Sunda-Banda", rendered_map)
        self.assertIn("Palung Papua Utara", rendered_map)
        self.assertIn("Trench / Tunjaman", rendered_map)
        self.assertIn("\\u25b2", rendered_map)
        self.assertNotIn("\\u25bc", rendered_map)
        self.assertIn('"offset": -3', rendered_map)
        self.assertIn('"font-size": "15"', rendered_map)
        self.assertIn("Legenda Tektonik", rendered_map)
        self.assertIn('class="map-legend map-legend-tectonic"', rendered_map)
        self.assertIn("<summary>Legenda Tektonik</summary>", rendered_map)
        self.assertIn("Sesar Geser", rendered_map)
        self.assertIn("Sesar Naik", rendered_map)
        self.assertIn("Sesar Turun / Normal", rendered_map)
        self.assertIn("FaultSymbolCanvasLayer", rendered_map)
        self.assertIn("fault-symbol-canvas-layer", rendered_map)
        self.assertIn("L.divIcon", rendered_map)
        self.assertIn("fault-name-label", rendered_map)
        reverse_feature_count = sum(
            str(feature.get("properties", {}).get("Type", "")).upper().startswith("R")
            for feature in fault_geojson["features"]
        )
        normal_feature_count = sum(
            str(feature.get("properties", {}).get("Type", "")).upper().startswith("N")
            for feature in fault_geojson["features"]
        )
        symbol_records = _fault_symbol_canvas_records(fault_geojson)
        self.assertGreaterEqual(
            sum(record[3] == 1 for record in symbol_records),
            reverse_feature_count * 3,
        )
        self.assertGreaterEqual(
            sum(record[3] == 2 for record in symbol_records),
            normal_feature_count * 3,
        )
        self.assertNotIn('"dashArray": "10 4"', rendered_map)
        self.assertNotIn('"dashArray": "2 7"', rendered_map)
        self.assertNotIn("Palung Sunda-Banda", hidden_map)

    def test_slab_contours_load_with_positive_depths(self) -> None:
        slab_geojson = load_slab_contours(SLAB_DATA_PATH)
        self.assertEqual(slab_geojson["type"], "FeatureCollection")
        self.assertTrue(slab_geojson["features"])
        depths = get_slab_depths(slab_geojson)
        self.assertTrue(all(depth > 0 for depth in depths))
        self.assertIn(100.0, depths)
        for feature in slab_geojson["features"]:
            self.assertEqual(feature["geometry"]["type"], "LineString")
            self.assertGreaterEqual(len(feature["geometry"]["coordinates"]), 2)

    def test_slab_interval_filter_keeps_only_multiples(self) -> None:
        slab_geojson = load_slab_contours(SLAB_DATA_PATH)
        filtered = filter_slab_contours(slab_geojson, interval_km=100)
        depths = get_slab_depths(filtered)
        self.assertTrue(depths)
        self.assertTrue(all(round(depth) % 100 == 0 for depth in depths))
        self.assertRaises(
            SlabDataError,
            load_slab_contours,
            SLAB_DATA_PATH.with_name("missing_slab.in"),
        )

    def test_map_renders_slab_contours_and_legend(self) -> None:
        slab_geojson = filter_slab_contours(
            load_slab_contours(SLAB_DATA_PATH),
            interval_km=100,
        )
        rendered_map = build_indonesia_map(
            slab_geojson=slab_geojson,
            show_slabs=True,
            show_basemap=False,
        ).get_root().render()
        hidden_map = build_indonesia_map(
            slab_geojson=slab_geojson,
            show_slabs=False,
            show_basemap=False,
        ).get_root().render()

        self.assertIn("Kontur Slab (Slab2)", rendered_map)
        self.assertIn("Kontur slab (km)", rendered_map)
        self.assertNotIn("Kontur Slab (Slab2)", hidden_map)

    def test_publication_map_includes_slab_contours(self) -> None:
        earthquake_df = clean_earthquake_data(
            pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        )
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        slab_geojson = filter_slab_contours(
            load_slab_contours(SLAB_DATA_PATH),
            interval_km=100,
        )
        png_bytes, pdf_bytes = render_publication_map(
            earthquake_df,
            fault_geojson,
            province_geojson,
            slab_geojson=slab_geojson,
            show_slabs=True,
            title="Peta Uji Slab",
            institution_name="Instansi Uji",
            show_basemap=False,
        )
        rendered_image = Image.open(BytesIO(png_bytes))
        self.assertEqual(rendered_image.format, "PNG")
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_clean_valid_sample_keeps_all_rows(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")

        clean_df = clean_earthquake_data(raw_df)

        self.assertEqual(len(clean_df), 6)
        self.assertIn("waktu", clean_df.columns)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(clean_df["waktu"]))

    def test_jakarta_synthetic_sample_has_100_clean_rows(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_jakarta_sintesis.csv")

        clean_df = clean_earthquake_data(raw_df)

        self.assertEqual(len(raw_df), 100)
        self.assertEqual(len(clean_df), 100)
        self.assertTrue(clean_df["lintang"].between(-6.6, -5.8).all())
        self.assertTrue(clean_df["bujur"].between(106.4, 107.3).all())

    def test_master_database_starts_from_seed_and_appends_without_duplicates(self) -> None:
        upload_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")

        with TemporaryDirectory() as temp_dir:
            master_path = Path(temp_dir) / "gempa_master.csv"
            seed_path = SAMPLE_DIR / "gempa_jakarta_sintesis.csv"

            seeded_df = load_master_database(master_path, seed_path)
            first_result = append_to_master_database(master_path, upload_df, seed_path)
            second_result = append_to_master_database(master_path, upload_df, seed_path)
            final_df = normalize_for_database(pd.read_csv(master_path))

        self.assertEqual(len(normalize_for_database(seeded_df)), 100)
        self.assertEqual(first_result["added_rows"], 6)
        self.assertEqual(first_result["total_rows"], 106)
        self.assertEqual(second_result["added_rows"], 0)
        self.assertEqual(second_result["total_rows"], 106)
        self.assertEqual(len(final_df), 106)

    def test_database_normalization_ignores_pg_and_preserves_descriptions(self) -> None:
        raw_df = pd.DataFrame(
            {
                "tanggal": ["2025-10-01"],
                "jam": ["00:42:32"],
                "lintang": [-8.1],
                "bujur": [107.32],
                "kedalaman": [13],
                "magnitudo": [2.3],
                "jenis_lokasi": ["laut "],
                "keterangan_lokasi": ["116 km BaratDaya KAB-GARUT-JABAR"],
                "dirasakan": [""],
            }
        )

        normalized_df = normalize_for_database(raw_df)

        self.assertNotIn("jenis_lokasi", normalized_df.columns)
        self.assertEqual(
            normalized_df.loc[0, "keterangan_lokasi"],
            "116 km BaratDaya KAB-GARUT-JABAR",
        )
        self.assertEqual(normalized_df.loc[0, "dirasakan"], "")

    def test_database_deduplication_merges_latest_nonempty_metadata(self) -> None:
        duplicate_rows = pd.DataFrame(
            {
                "tanggal": ["2025-09-26", "2025-09-26"],
                "jam": ["22:08:55", "22:08:55"],
                "lintang": [-6.75, -6.75],
                "bujur": [106.57, 106.57],
                "kedalaman": [9, 9],
                "magnitudo": [3.0, 3.0],
                "keterangan_lokasi": ["Sukabumi", ""],
                "dirasakan": ["", "Kabandungan II-III MMI"],
            }
        )

        deduplicated = deduplicate_database_records(duplicate_rows)

        self.assertEqual(len(deduplicated), 1)
        self.assertNotIn("jenis_lokasi", deduplicated.columns)
        self.assertEqual(deduplicated.loc[0, "keterangan_lokasi"], "Sukabumi")
        self.assertEqual(
            deduplicated.loc[0, "dirasakan"],
            "Kabandungan II-III MMI",
        )

    def test_loading_master_repairs_existing_duplicate_events(self) -> None:
        duplicate_rows = pd.DataFrame(
            {
                "tanggal": ["2025-09-26", "2025-09-26"],
                "jam": ["22:08:55", "22:08:55"],
                "lintang": [-6.75, -6.75],
                "bujur": [106.57, 106.57],
                "kedalaman": [9, 9],
                "magnitudo": [3.0, 3.0],
                "keterangan_lokasi": ["Sukabumi", "Sukabumi"],
                "dirasakan": ["", "Kabandungan II-III MMI"],
            }
        )

        with TemporaryDirectory() as temp_dir:
            master_path = Path(temp_dir) / "gempa_master.csv"
            duplicate_rows.to_csv(master_path, index=False)

            loaded = load_master_database(master_path)
            persisted = pd.read_csv(master_path).fillna("")

        self.assertEqual(len(loaded), 1)
        self.assertEqual(len(persisted), 1)
        self.assertEqual(loaded.loc[0, "dirasakan"], "Kabandungan II-III MMI")

    def test_loading_clean_master_skips_duplicate_repair_groupby(self) -> None:
        clean_rows = pd.DataFrame(
            {
                "tanggal": ["2025-09-26", "2025-09-27"],
                "jam": ["22:08:55", "23:09:56"],
                "lintang": [-6.75, -6.80],
                "bujur": [106.57, 106.60],
                "kedalaman": [9, 12],
                "magnitudo": [3.0, 3.2],
                "keterangan_lokasi": ["Sukabumi", "Selatan Jawa"],
                "dirasakan": ["", ""],
            }
        )

        with TemporaryDirectory() as temp_dir:
            master_path = Path(temp_dir) / "gempa_master.csv"
            clean_rows.to_csv(master_path, index=False)
            with patch(
                "utils.data_loader.deduplicate_database_records",
                side_effect=AssertionError("repair path should not run"),
            ):
                loaded = load_master_database(master_path)

        self.assertEqual(len(loaded), 2)

    def test_duplicate_import_updates_metadata_without_negative_added_count(self) -> None:
        base = pd.DataFrame(
            {
                "tanggal": ["2025-09-26"],
                "jam": ["22:08:55"],
                "lintang": [-6.75],
                "bujur": [106.57],
                "kedalaman": [9],
                "magnitudo": [3.0],
                "dirasakan": [""],
            }
        )
        update = base.copy()
        update["dirasakan"] = "Kabandungan II-III MMI"

        with TemporaryDirectory() as temp_dir:
            master_path = Path(temp_dir) / "gempa_master.csv"
            save_master_database(master_path, base)

            result = append_to_master_database(master_path, update)
            persisted = pd.read_csv(master_path).fillna("")

        self.assertEqual(result["added_rows"], 0)
        self.assertEqual(result["merged_rows"], 1)
        self.assertEqual(result["total_rows"], 1)
        self.assertEqual(persisted.loc[0, "dirasakan"], "Kabandungan II-III MMI")

    def test_clean_mixed_sample_drops_invalid_rows_silently(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_mixed_invalid.csv")

        clean_df = clean_earthquake_data(raw_df)

        self.assertEqual(len(clean_df), 2)
        self.assertEqual(clean_df["magnitudo"].tolist(), [4.8, 5.9])

    def test_missing_required_column_raises_explicit_error(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_missing_columns.csv")

        with self.assertRaises(DataValidationError) as context:
            validate_required_columns(raw_df)

        self.assertIn("magnitudo", str(context.exception))

    def test_filter_uses_depth_magnitude_and_inclusive_date_range(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        clean_df = clean_earthquake_data(raw_df)

        filtered_df = filter_earthquake_data(
            clean_df,
            depth_range=(0.0, 100.0),
            magnitude_range=(4.5, 6.0),
            date_range=(pd.Timestamp("2026-01-04"), pd.Timestamp("2026-01-15")),
        )

        self.assertEqual(len(filtered_df), 2)
        self.assertEqual(filtered_df["tanggal"].tolist(), ["2026-01-04", "2026-01-06"])

    def test_fault_reference_enriches_point_on_fault(self) -> None:
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        first_feature = fault_geojson["features"][0]
        coordinates = first_feature["geometry"]["coordinates"]
        if first_feature["geometry"]["type"] == "MultiLineString":
            coordinates = coordinates[0]
        longitude, latitude = coordinates[0][:2]
        earthquake_df = pd.DataFrame(
            {
                "lintang": [latitude],
                "bujur": [longitude],
            }
        )

        enriched_df = enrich_earthquakes_with_faults(earthquake_df, fault_geojson)

        self.assertEqual(enriched_df.loc[0, "sesar"], first_feature["properties"]["Name"])
        self.assertAlmostEqual(enriched_df.loc[0, "jarak_sesar_km"], 0.0, places=2)

    def test_filter_by_distance_to_selected_fault(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        clean_df = clean_earthquake_data(raw_df)
        distances = pd.Series(
            [2.0, 12.0, 4.0, 25.0, 8.0, 30.0],
            index=clean_df.index,
        )

        filtered_df = filter_earthquake_data(
            clean_df,
            depth_range=(0.0, 700.0),
            magnitude_range=(0.0, 10.0),
            date_range=(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31")),
            fault_distances=distances,
            max_fault_distance=10.0,
        )

        # Only events within 10 km of the selected fault remain (2, 4, 8 km).
        self.assertEqual(len(filtered_df), 3)

    def test_distance_to_selected_fault_uses_chosen_line(self) -> None:
        fault_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"Name": "Uji"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[107.0, -6.0], [107.0, -7.0]],
                    },
                }
            ],
        }
        df = pd.DataFrame({"lintang": [-6.5], "bujur": [107.1]})

        distances = distance_to_faults(df, fault_geojson, ["Uji"])
        expected_km = 0.1 * 111.320 * math.cos(math.radians(-6.5))
        self.assertAlmostEqual(distances[0], expected_km, places=1)

        # An unselected fault name yields no distance (NaN) so nothing is kept.
        self.assertTrue(np.all(np.isnan(distance_to_faults(df, fault_geojson, ["X"]))))

    def test_fault_segment_options_union_only_selected_faults(self) -> None:
        fault_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar A", "Segment": "Utara"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[100.0, 0.0], [100.0, 1.0]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar A", "Segment": "Selatan"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[101.0, 0.0], [101.0, 1.0]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar B", "Segment": "Utara"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[102.0, 0.0], [102.0, 1.0]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar C", "Segment": "Tengah"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[103.0, 0.0], [103.0, 1.0]],
                    },
                },
            ],
        }

        options = get_fault_segment_options(
            fault_geojson,
            ["Sesar A", "Sesar B"],
        )

        self.assertEqual(set(options), {0, 1, 2})
        self.assertEqual(options[0], "Utara")
        self.assertEqual(options[2], "Utara")
        self.assertNotIn(3, options)

    def test_distance_to_selected_fault_segments_uses_only_feature_indexes(self) -> None:
        fault_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar Uji", "Segment": "Barat"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[100.0, -1.0], [100.0, 1.0]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"Name": "Sesar Uji", "Segment": "Timur"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[110.0, -1.0], [110.0, 1.0]],
                    },
                },
            ],
        }
        df = pd.DataFrame(
            {"lintang": [0.0, 0.0], "bujur": [100.1, 109.9]}
        )

        west_distances = distance_to_fault_segments(df, fault_geojson, [0])
        east_distances = distance_to_fault_segments(df, fault_geojson, [1])

        self.assertLess(west_distances[0], 12.0)
        self.assertGreater(west_distances[1], 1000.0)
        self.assertGreater(east_distances[0], 1000.0)
        self.assertLess(east_distances[1], 12.0)
        self.assertTrue(
            np.all(np.isnan(distance_to_fault_segments(df, fault_geojson, [])))
        )

    def test_fault_distance_uses_wgs84_latitude_scale(self) -> None:
        fault_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"Name": "Khatulistiwa"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[100.0, 0.0], [101.0, 0.0]],
                    },
                }
            ],
        }
        df = pd.DataFrame({"lintang": [-10.0], "bujur": [100.0]})

        distance = distance_to_faults(df, fault_geojson, ["Khatulistiwa"])[0]

        # Independent WGS84 meridian reference: 1,105.8548 km.  A fixed
        # equatorial km/degree factor misses this tolerance.
        self.assertAlmostEqual(distance, 1105.8548, delta=0.05)

    def test_fault_distance_handles_antimeridian_segments(self) -> None:
        fault_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"Name": "Lintas Antimeridian"},
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[179.0, -2.0], [-179.0, 0.0]],
                    },
                }
            ],
        }
        df = pd.DataFrame({"lintang": [-1.0], "bujur": [179.8]})

        distance = distance_to_faults(
            df,
            fault_geojson,
            ["Lintas Antimeridian"],
        )[0]

        # The nearest point is across the date line, not ~360 degrees away.
        self.assertAlmostEqual(distance, 15.7256, delta=0.05)

    def test_distance_to_selected_fault_matches_nearest_reference(self) -> None:
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        df = pd.DataFrame({"lintang": [-6.95, -7.5], "bujur": [107.6, 110.2]})
        enriched = enrich_earthquakes_with_faults(df, fault_geojson)

        for position in range(len(enriched)):
            name = enriched.loc[position, "sesar"]
            if name == "Tanpa nama":
                continue
            own = distance_to_faults(
                enriched.iloc[[position]], fault_geojson, [name]
            )[0]
            self.assertAlmostEqual(
                own,
                float(enriched.loc[position, "jarak_sesar_km"]),
                places=1,
            )
            self.assertIn(name, get_fault_names(fault_geojson))

    def test_region_reference_assigns_known_city_points(self) -> None:
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        district_geojson = load_region_geojson(DISTRICT_DATA_PATH)
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-6.2088, -6.9175, -7.7956],
                "bujur": [106.8456, 107.6191, 110.3695],
                "jenis_lokasi": ["Laut", "Laut", "Laut"],
            }
        )

        enriched_df = enrich_earthquakes_with_regions(
            earthquake_df,
            province_geojson,
            district_geojson,
        )

        self.assertEqual(
            enriched_df["provinsi"].tolist(),
            ["DKI Jakarta", "Jawa Barat", "Daerah Istimewa Yogyakarta"],
        )
        self.assertEqual(
            enriched_df["kabupaten_kota"].tolist(),
            [
                "Kota Administrasi Jakarta Selatan",
                "Kota Bandung",
                "Kota Yogyakarta",
            ],
        )
        self.assertEqual(enriched_df["jenis_lokasi"].tolist(), ["Darat"] * 3)
        self.assertEqual(enriched_df["jarak_darat_km"].tolist(), [0.0] * 3)

    def test_region_reference_assigns_nearby_sea_to_nearest_province(self) -> None:
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        district_geojson = load_region_geojson(DISTRICT_DATA_PATH)
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-5.9],
                "bujur": [106.85],
                "jenis_lokasi": ["Darat"],
            }
        )

        enriched_df = enrich_earthquakes_with_regions(
            earthquake_df,
            province_geojson,
            district_geojson,
        )

        self.assertEqual(enriched_df.loc[0, "jenis_lokasi"], "Laut")
        self.assertNotEqual(enriched_df.loc[0, "provinsi"], "Di luar wilayah administrasi")
        self.assertGreater(enriched_df.loc[0, "jarak_darat_km"], 0.0)

    def test_filter_supports_province_and_district(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        clean_df = clean_earthquake_data(raw_df)
        clean_df["provinsi"] = ["A", "A", "B", "B", "C", "C"]
        clean_df["kabupaten_kota"] = ["A1", "A2", "B1", "B2", "C1", "C2"]

        filtered_df = filter_earthquake_data(
            clean_df,
            depth_range=(0.0, 700.0),
            magnitude_range=(0.0, 10.0),
            date_range=(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31")),
            province_names=["B"],
            district_names=["B2"],
        )

        self.assertEqual(len(filtered_df), 1)
        self.assertEqual(filtered_df.loc[0, "provinsi"], "B")
        self.assertEqual(filtered_df.loc[0, "kabupaten_kota"], "B2")

    def test_filter_supports_land_and_sea_location(self) -> None:
        raw_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        clean_df = clean_earthquake_data(raw_df)
        clean_df["jenis_lokasi"] = ["Darat", "Laut", "Laut", "Darat", "Laut", "Darat"]

        filtered_df = filter_earthquake_data(
            clean_df,
            depth_range=(0.0, 700.0),
            magnitude_range=(0.0, 10.0),
            date_range=(pd.Timestamp("2026-01-01"), pd.Timestamp("2026-12-31")),
            location_types=["Laut"],
        )

        self.assertEqual(len(filtered_df), 3)
        self.assertTrue((filtered_df["jenis_lokasi"] == "Laut").all())

    def test_summary_stats_match_filtered_rows(self) -> None:
        df = pd.DataFrame({"magnitudo": [4.8, 5.2, 5.9]})

        stats = calculate_summary_stats(df)

        self.assertEqual(stats["jumlah_gempa"], 3)
        self.assertAlmostEqual(stats["magnitudo_rata_rata"], 5.3)
        self.assertEqual(stats["magnitudo_maksimum"], 5.9)

    def test_depth_colors_use_non_overlapping_boundaries(self) -> None:
        self.assertEqual(get_depth_style(50)[0], "#ed2939")
        self.assertEqual(get_depth_style(50.01)[0], "#f04b23")
        self.assertEqual(get_depth_style(100)[0], "#f04b23")
        self.assertEqual(get_depth_style(100.01)[0], "#f5a623")
        self.assertEqual(get_depth_style(250)[0], "#f5a623")
        self.assertEqual(get_depth_style(250.01)[0], "#087830")
        self.assertEqual(get_depth_style(600)[0], "#087830")
        self.assertEqual(get_depth_style(600.01)[0], "#2534d2")

    def test_magnitude_markers_use_classes_one_through_eight_plus(self) -> None:
        radii = [get_magnitude_marker(float(value))[0] for value in range(1, 9)]

        self.assertEqual(get_magnitude_marker(1.99)[1], "1")
        self.assertEqual(get_magnitude_marker(2.0)[1], "2")
        self.assertEqual(get_magnitude_marker(7.99)[1], "7")
        self.assertEqual(get_magnitude_marker(8.0)[1], "8+")
        self.assertTrue(all(left < right for left, right in zip(radii, radii[1:])))

    def test_magnitude_legend_uses_same_scaled_radius_as_plotted_points(self) -> None:
        marker_scale = 1.5
        magnitude = 5.2
        radius = get_scaled_magnitude_radius(magnitude, marker_scale)
        diameter = radius * 2
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-6.2],
                "bujur": [106.8],
                "kedalaman": [10.0],
                "magnitudo": [magnitude],
                "tanggal": ["2026-01-01"],
                "jam": ["12:00:00"],
            }
        )

        rendered_map = build_indonesia_map(
            earthquake_df,
            earthquake_marker_scale=marker_scale,
        ).get_root().render()

        self.assertIn(f"[-6.2,106.8,{radius:g},0", rendered_map)
        self.assertIn(f'data-radius-px="{radius:g}"', rendered_map)
        self.assertIn(
            f"width:{diameter:g}px;height:{diameter:g}px",
            rendered_map,
        )

    def test_publication_legend_radius_matches_plotted_marker_exactly(self) -> None:
        # The publication legend circle must be the exact size of the markers
        # it describes (same value the map snapshot and the dashboard plot),
        # not a floored approximation.
        for magnitude in (1.0, 2.0, 4.0, 8.0):
            for marker_scale in (0.5, 1.0, 1.4145):
                self.assertEqual(
                    _publication_magnitude_radius(magnitude, marker_scale),
                    get_scaled_magnitude_radius(magnitude, marker_scale),
                )

    def test_snapshot_marker_scale_matches_legend_page_factor(self) -> None:
        # The sidebar magnitude legend and the direct-canvas markers scale by
        # the layout's page factor; capture_filtered_map must apply the same
        # factor to the snapshot so its markers match the legend circles on the
        # larger A3 page too, not only on A4.
        for page_size in PAGE_SIZES:
            layout_scale = _publication_layout(page_size)[0]
            snapshot_scale = (
                PAGE_SIZES[page_size][0] / PAGE_SIZES["A4 Lanskap"][0]
            )
            self.assertAlmostEqual(layout_scale, snapshot_scale)
        self.assertAlmostEqual(
            _publication_layout("A4 Lanskap", 300.0)[0],
            300.0 / OUTPUT_DPI,
            places=3,
        )

    def test_publication_filename_uses_title_with_underscores(self) -> None:
        self.assertEqual(
            publication_filename_stem("Peta Episenter\nJawa Barat: 2026"),
            "Peta_Episenter_Jawa_Barat_2026",
        )
        self.assertEqual(
            publication_filename_stem("  Peta___Gempa / Indonesia  "),
            "Peta_Gempa_Indonesia",
        )
        self.assertEqual(publication_filename_stem("***"), "Peta_Publikasi")

    def test_map_uses_opentopomap_without_api_key(self) -> None:
        rendered_map = build_indonesia_map(
            basemap_name=BASEMAP_OPENTOPO,
        ).get_root().render()

        self.assertIn("tile.opentopomap.org", rendered_map)
        self.assertIn("OpenTopoMap", rendered_map)

    def test_map_uses_cyclosm_basemap(self) -> None:
        rendered_map = build_indonesia_map(
            basemap_name=BASEMAP_CYCLOSM,
        ).get_root().render()

        self.assertIn("tile-cyclosm.openstreetmap.fr/cyclosm", rendered_map)
        self.assertIn("CyclOSM", rendered_map)

    def test_map_uses_humanitarian_basemap(self) -> None:
        rendered_map = build_indonesia_map(
            basemap_name=BASEMAP_HUMANITARIAN,
        ).get_root().render()

        self.assertIn("tile.openstreetmap.fr/hot", rendered_map)
        self.assertIn("Humanitarian", rendered_map)

    def test_map_uses_shortbread_vector_tiles(self) -> None:
        rendered_map = build_indonesia_map(
            basemap_name=BASEMAP_SHORTBREAD,
        ).get_root().render()

        self.assertIn("tiles.versatiles.org/tiles/osm", rendered_map)
        self.assertIn("L.vectorGrid.protobuf", rendered_map)
        self.assertIn("rendererFactory: L.canvas.tile", rendered_map)
        self.assertIn("keepBuffer: 2", rendered_map)
        self.assertIn("shortbread-base-pane", rendered_map)
        self.assertIn("leaflet.vectorgrid@1.3.0", rendered_map)
        self.assertNotIn("leaflet.vectorgrid@latest", rendered_map)
        self.assertIn("Shortbread", rendered_map)
        self.assertIn("streets: function() { return []; }", rendered_map)
        self.assertIn("water_lines: {color: '#327fa3'", rendered_map)
        self.assertNotIn("tiles.versatiles.org/tiles/hillshade-vectors", rendered_map)
        self.assertNotIn("Elevation/World_Hillshade/MapServer", rendered_map)
        self.assertNotIn("var major", rendered_map)

    def test_shortbread_topography_can_be_enabled(self) -> None:
        earthquake_data = pd.DataFrame(
            {
                "lintang": [-6.2, -7.8],
                "bujur": [106.8, 110.4],
                "magnitudo": [4.2, 5.1],
                "kedalaman_km": [20, 90],
            }
        )
        rendered_map = build_indonesia_map(
            df=earthquake_data,
            basemap_name=BASEMAP_SHORTBREAD,
            show_shortbread_topography=True,
            show_earthquakes=False,
            show_earthquake_legend=False,
        ).get_root().render()

        self.assertIn("tiles.versatiles.org/tiles/osm", rendered_map)
        self.assertIn("Elevation/World_Hillshade/MapServer", rendered_map)
        self.assertIn('"opacity": 0.5', rendered_map)
        self.assertIn('"keepBuffer": 0', rendered_map)
        self.assertNotIn("properties.shade === 'dark'", rendered_map)
        self.assertIn("shortbread-hillshade-pane", rendered_map)
        self.assertIn("mixBlendMode = 'multiply'", rendered_map)
        self.assertLess(
            rendered_map.index(".fitBounds("),
            rendered_map.index("L.vectorGrid.protobuf"),
        )
        self.assertIn('"animate": false', rendered_map)

    def test_shortbread_topography_applies_warm_terrain_palette(self) -> None:
        topographic_map = build_indonesia_map(
            basemap_name=BASEMAP_SHORTBREAD,
            show_shortbread_topography=True,
            show_earthquake_legend=False,
        ).get_root().render()
        plain_map = build_indonesia_map(
            basemap_name=BASEMAP_SHORTBREAD,
            show_shortbread_topography=False,
            show_earthquake_legend=False,
        ).get_root().render()

        self.assertIn("#e6dec6", topographic_map)
        self.assertIn("forest: '#a7c689'", topographic_map)
        self.assertNotIn("#e6dec6", plain_map)
        self.assertIn("forest: '#b7d39f'", plain_map)

    def test_slider_bounds_stay_adjustable_for_single_value(self) -> None:
        from app import safe_slider_bounds

        # Single unique value would freeze a Streamlit slider (min == max).
        self.assertEqual(safe_slider_bounds(28.0, 28.0, 1.0), (28.0, 29.0))
        low, high = safe_slider_bounds(4.4, 4.4, 0.1)
        self.assertLess(low, high)
        # Genuine ranges are left untouched.
        self.assertEqual(safe_slider_bounds(2.0, 263.0, 1.0), (2.0, 263.0))
        # Non-finite input falls back to a usable range.
        self.assertEqual(safe_slider_bounds(float("nan"), 5.0, 1.0), (0.0, 1.0))

    def test_district_filter_options_include_all_west_java_cities(self) -> None:
        from app import get_district_filter_options

        district_geojson = load_region_geojson(DISTRICT_DATA_PATH)
        options = get_district_filter_options(district_geojson, ["Jawa Barat"])

        self.assertEqual(len(options), 27)
        self.assertIn("Kota Bandung", options)
        self.assertIn("Kota Depok", options)
        self.assertIn("Kota Tasikmalaya", options)

    def test_relief_basemap_uses_detailed_topographic_tiles(self) -> None:
        rendered_map = build_indonesia_map(
            basemap_name=BASEMAP_RELIEF,
            show_earthquake_legend=False,
        ).get_root().render()

        self.assertIn("World_Topo_Map", rendered_map)
        self.assertIn('"maxNativeZoom": 19', rendered_map)
        self.assertNotIn("World_Physical_Map", rendered_map)
        self.assertNotIn("reliefHillshadePane", rendered_map)

    def test_overview_inset_zooms_to_region(self) -> None:
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        data_bounds = (106.0, -8.0, 108.0, -6.0)
        west, south, east, north = _overview_bounds(data_bounds, (300, 180))

        self.assertLess(west, 106.0)
        self.assertGreater(east, 108.0)
        self.assertLess(east - west, 47.0)
        self.assertGreater(east - west, 2.0)

        inset = _render_overview_map(province_geojson, data_bounds, (300, 180))
        self.assertEqual(inset.size, (300, 180))

    def test_publication_bounds_match_web_mercator_map_aspect(self) -> None:
        original = (105.6272, -10.5656, 109.5828, -5.7544)
        size = (1077, 965)

        fitted = _fit_bounds_to_web_mercator_aspect(original, size)
        left, top = _mercator_pixel(fitted[0], fitted[3], 0)
        right, bottom = _mercator_pixel(fitted[2], fitted[1], 0)

        self.assertAlmostEqual(
            (right - left) / (bottom - top),
            size[0] / size[1],
            places=10,
        )
        self.assertLess(fitted[0], original[0])
        self.assertGreater(fitted[2], original[2])

    def test_coordinate_labels_use_degree_minute_second_hemispheres(self) -> None:
        self.assertEqual(_format_coordinate_label(124.0, "E", "W"), "124°00′00″E")
        self.assertEqual(_format_coordinate_label(-3.5, "N", "S"), "3°30′00″S")

    def test_coordinate_grid_draws_alternating_frame_outside_map(self) -> None:
        canvas = Image.new("RGB", (520, 380), "white")
        draw = ImageDraw.Draw(canvas, "RGBA")
        map_box = (70, 60, 380, 260)
        draw.rectangle(
            (
                map_box[0],
                map_box[1],
                map_box[0] + map_box[2],
                map_box[1] + map_box[3],
            ),
            fill="#8fc6dd",
        )

        _draw_coordinate_grid(canvas, draw, (100.0, -10.0, 110.0, 0.0), map_box)

        top_frame_colors = {
            canvas.getpixel((sample_x, map_box[1] - 4))
            for sample_x in range(map_box[0] + 2, map_box[0] + map_box[2] - 1)
        }
        self.assertIn((255, 255, 255), top_frame_colors)
        self.assertIn((16, 42, 114), top_frame_colors)
        self.assertEqual(canvas.getpixel((map_box[0] + 20, map_box[1] + 20)), (143, 198, 221))

    def test_publication_page_pixels_match_pdf_output_dpi(self) -> None:
        a4_width, a4_height = PAGE_SIZES["A4 Lanskap"]
        a3_width, a3_height = PAGE_SIZES["A3 Lanskap"]

        self.assertAlmostEqual(a4_width / OUTPUT_DPI, 11.69, places=2)
        self.assertAlmostEqual(a4_height / OUTPUT_DPI, 8.27, places=2)
        self.assertAlmostEqual(a3_width / OUTPUT_DPI, 16.54, places=2)
        self.assertAlmostEqual(a3_height / OUTPUT_DPI, 11.69, places=2)
        self.assertEqual(PUBLICATION_DPI_OPTIONS["Cetak (300 DPI)"], 300.0)
        self.assertEqual(
            get_publication_page_size("A4 Lanskap", 300.0),
            (3508, 2481),
        )
        self.assertEqual(
            get_publication_page_size("A3 Lanskap", 300.0),
            (4962, 3508),
        )
        self.assertEqual(_publication_tile_limit(300.0), 104)
        self.assertGreater(
            _publication_tile_limit(144.0, map_size=(2_000, 1_300)),
            _publication_tile_limit(144.0, map_size=(1_000, 650)),
        )

    def test_map_supports_fractional_zoom_for_exact_publication_extent(self) -> None:
        rendered_map = build_indonesia_map(zoom_snap=0.0).get_root().render()

        self.assertIn('"zoomSnap": 0.0', rendered_map)

    def test_hidden_earthquake_layer_drops_its_source_credit(self) -> None:
        features = {"type": "FeatureCollection", "features": [{"type": "Feature"}]}

        sources = _publication_source_lines(
            "Katalog Gempa Uji",
            "Topografi: Esri World Topographic Map",
            show_basemap=True,
            show_earthquakes=False,
            show_faults=False,
            show_trenches=False,
            show_slabs=False,
            show_population_density=False,
            show_provinces=True,
            show_inset=False,
            fault_geojson=features,
            trench_geojson=None,
            slab_geojson=None,
        )

        # Crediting a catalogue on a sheet that plots no epicentres would imply
        # the map shows the events in it.
        self.assertFalse(any("Katalog Gempa Uji" in source for source in sources))
        self.assertFalse(any(source.startswith("Data gempa") for source in sources))
        # The attribution already names its layer, so it must not be prefixed twice.
        self.assertIn(
            "Basemap Topografi: Esri World Topographic Map", sources
        )
        self.assertTrue(
            any(source.startswith("Batas administrasi:") for source in sources)
        )

    def test_publication_sources_follow_visible_layers(self) -> None:
        features = {"type": "FeatureCollection", "features": [{"type": "Feature"}]}

        sources = _publication_source_lines(
            "Katalog Gempa Uji",
            "Basemap Uji",
            show_basemap=False,
            show_faults=True,
            show_trenches=False,
            show_slabs=True,
            show_population_density=True,
            show_provinces=False,
            show_inset=True,
            fault_geojson=features,
            trench_geojson=features,
            slab_geojson=features,
        )

        self.assertEqual(sources[0], "Data gempa: Katalog Gempa Uji")
        self.assertTrue(any(source.startswith("Sesar:") for source in sources))
        # Credits use the same wording as the legend entries they explain.
        self.assertTrue(any(source.startswith("Kontur slab:") for source in sources))
        self.assertTrue(
            any(source.startswith("Kepadatan penduduk:") for source in sources)
        )
        self.assertTrue(
            any("BPS kabupaten/kota" in source for source in sources)
        )
        self.assertTrue(
            any(source.startswith("Batas administrasi:") for source in sources)
        )
        self.assertFalse(any(source.startswith("Basemap:") for source in sources))
        self.assertFalse(any(source.startswith("Trench:") for source in sources))

    def test_locator_inset_stays_in_sidebar_when_shortbread_legend_is_full(self) -> None:
        canvas = Image.new("RGB", (900, 650), "#e5e7eb")
        draw = ImageDraw.Draw(canvas, "RGBA")
        province_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"PROVINSI": "Uji"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[105, -8], [111, -8], [111, -5], [105, -5], [105, -8]]
                        ],
                    },
                }
            ],
        }

        # A full legend leaves no room at all between it and the source block,
        # so the locator is omitted rather than drawn over either of them - and
        # it never falls back into the map area.
        placement = _draw_locator_inset(
            canvas,
            draw,
            province_geojson,
            (106.0, -7.8, 110.0, -5.8),
            (640, 20, 240, 600),
            inset_top=510,
            source_y=520,
            scale=1.0,
            label_font=ImageFont.load_default(),
        )

        self.assertEqual(placement, "none")
        self.assertEqual(canvas.getpixel((100, 100)), (229, 231, 235))

        # With room to spare it is drawn, still inside the sidebar.
        placement = _draw_locator_inset(
            canvas,
            draw,
            province_geojson,
            (106.0, -7.8, 110.0, -5.8),
            (640, 20, 240, 600),
            inset_top=300,
            source_y=520,
            scale=1.0,
            label_font=ImageFont.load_default(),
        )

        self.assertEqual(placement, "sidebar")
        self.assertEqual(canvas.getpixel((100, 100)), (229, 231, 235))

    def test_sidebar_legends_compact_only_when_locator_space_is_tight(self) -> None:
        shared = {
            "side_y": 188,
            "side_height": 965,
            "scale": 1.0,
            "logo_top": 212,
            "base_logo_area_height": 90,
        }

        self.assertTrue(
            _should_compact_sidebar_legends(
                **shared,
                normal_legend_block_height=600,
            )
        )
        self.assertFalse(
            _should_compact_sidebar_legends(
                **shared,
                normal_legend_block_height=360,
            )
        )

    def test_publication_scale_bar_is_centered_in_its_sidebar_box(self) -> None:
        canvas = Image.new("RGB", (600, 140), "white")
        draw = ImageDraw.Draw(canvas)
        box = (100, 70, 400, 55)

        _draw_scale_bar(draw, (106.0, -8.0, 110.0, -6.0), 1000, box)

        bar_pixels = [
            x
            for x in range(box[0], box[0] + box[2])
            if canvas.getpixel((x, box[1] + 9)) != (255, 255, 255)
        ]
        self.assertTrue(bar_pixels)
        bar_center = (min(bar_pixels) + max(bar_pixels)) / 2.0
        box_center = box[0] + box[2] / 2.0
        self.assertLess(abs(bar_center - box_center), box[2] * 0.08)
        self.assertLess(
            max(bar_pixels) - min(bar_pixels),
            box[2] * 0.42,
            "Batang skala harus tetap ringkas terhadap lebar sidebar.",
        )

    def test_publication_scale_bar_labels_clear_the_bar_at_300_dpi(self) -> None:
        dpi_scale = 300.0 / OUTPUT_DPI
        canvas = Image.new("RGB", (1200, 320), "white")
        draw = ImageDraw.Draw(canvas)
        box = (120, 180, 900, round(55 * dpi_scale))

        _draw_scale_bar(
            draw,
            (106.0, -8.0, 110.0, -6.0),
            2200,
            box,
            scale=dpi_scale,
        )

        # The font, bar height, outline, and label gap must all scale together.
        # A fixed 23 px offset made the 300 DPI labels overlap the bar.
        clearance_height = max(8, round(11 * dpi_scale))
        clearance_pixels = [
            canvas.getpixel((x, y))
            for y in range(box[1] - clearance_height, box[1])
            for x in range(box[0], box[0] + box[2])
        ]
        self.assertTrue(all(pixel == (255, 255, 255) for pixel in clearance_pixels))
        scaled_bar_bottom = box[1] + round(12 * dpi_scale) - 1
        self.assertTrue(
            any(
                canvas.getpixel((x, scaled_bar_bottom)) != (255, 255, 255)
                for x in range(box[0], box[0] + box[2])
            )
        )

    def test_north_arrow_does_not_add_a_rectangular_background(self) -> None:
        background = (143, 198, 221)
        canvas = Image.new("RGB", (180, 180), background)

        _draw_north_arrow(canvas, (40, 30, 70, 110))

        self.assertEqual(canvas.getpixel((40, 30)), background)
        self.assertEqual(canvas.getpixel((109, 30)), background)
        self.assertEqual(canvas.getpixel((40, 139)), background)
        self.assertEqual(canvas.getpixel((109, 139)), background)

    def test_snapshot_renders_without_launching_a_browser(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "tanggal": ["2026-01-01"],
                "jam": ["12:00:00"],
                "lintang": [-6.2],
                "bujur": [106.8],
                "kedalaman": [10.0],
                "magnitudo": [4.0],
            }
        )
        fake_basemap = Image.new("RGB", (320, 240), "#b9d7e8")
        with patch(
            "utils.publication_map._render_basemap",
            return_value=(fake_basemap, "Uji basemap", 8),
        ) as renderer:
            snapshot = capture_filtered_map(
                earthquake_df,
                {"type": "FeatureCollection", "features": []},
                {"type": "FeatureCollection", "features": []},
                output_size=(320, 240),
                padding_percent=12,
                marker_scale_percent=100,
                show_basemap=True,
                show_faults=False,
                show_trenches=False,
                show_provinces=False,
            )

        renderer.assert_called_once()
        self.assertTrue(snapshot.startswith(b"\x89PNG"))
        with Image.open(BytesIO(snapshot)) as rendered:
            self.assertEqual(rendered.size, (320, 240))

    def test_shortbread_snapshot_uses_its_own_vector_provider(self) -> None:
        fake_basemap = Image.new("RGB", (320, 240), "#b9d7e8")
        with patch(
            "utils.publication_map._render_shortbread_basemap",
            return_value=(fake_basemap, "Shortbread attribution", 8),
        ) as renderer:
            image, attribution, zoom = _render_basemap(
                (106.0, -7.0, 108.0, -5.0),
                (320, 240),
                BASEMAP_SHORTBREAD,
            )

        renderer.assert_called_once_with(
            (106.0, -7.0, 108.0, -5.0),
            (320, 240),
            show_roads=False,
            show_rivers=True,
            topographic=False,
            progress_callback=None,
            max_tile_count=MAX_PUBLICATION_TILE_COUNT,
        )
        self.assertEqual(image.size, (320, 240))
        self.assertEqual(zoom, 8)
        self.assertIn("Shortbread", attribution)

    def test_failed_basemap_provider_is_not_replaced_by_another_provider(self) -> None:
        with patch(
            "utils.publication_map._render_raster_basemap",
            side_effect=PublicationMapError("provider uji gagal"),
        ) as renderer:
            with self.assertRaisesRegex(PublicationMapError, "Tidak ada file kosong"):
                _render_basemap(
                    (106.0, -7.0, 108.0, -5.0),
                    (320, 240),
                    BASEMAP_OPENTOPO,
                    retry_timeout_seconds=0,
                    max_retry_rounds=1,
                )

        renderer.assert_called_once_with(
            (106.0, -7.0, 108.0, -5.0),
            (320, 240),
            BASEMAP_OPENTOPO,
            progress_callback=None,
            max_tile_count=MAX_PUBLICATION_TILE_COUNT,
        )

    def test_failed_basemap_does_not_return_an_offline_canvas(self) -> None:
        with patch(
            "utils.publication_map._render_raster_basemap",
            side_effect=PublicationMapError("semua provider uji gagal"),
        ):
            with self.assertRaises(PublicationMapError):
                _render_basemap(
                    (106.0, -7.0, 108.0, -5.0),
                    (320, 240),
                    BASEMAP_CYCLOSM,
                    retry_timeout_seconds=0,
                    max_retry_rounds=1,
                )

    def test_incomplete_basemap_retries_only_the_same_provider(self) -> None:
        fake_basemap = Image.new("RGB", (320, 240), "#b9d7e8")
        progress_events: list[tuple[str, int, int]] = []
        with (
            patch(
                "utils.publication_map._render_raster_basemap",
                side_effect=[
                    PublicationMapError("satu tile belum lengkap"),
                    (fake_basemap, "OpenTopoMap", 8),
                ],
            ) as renderer,
            patch("utils.publication_map.time.sleep") as sleeper,
        ):
            image, attribution, zoom = _render_basemap(
                (106.0, -7.0, 108.0, -5.0),
                (320, 240),
                BASEMAP_OPENTOPO,
                progress_callback=lambda message, completed, total: progress_events.append(
                    (message, completed, total)
                ),
                retry_timeout_seconds=30,
                max_retry_rounds=2,
            )

        self.assertEqual(renderer.call_count, 2)
        sleeper.assert_called_once()
        self.assertIs(image, fake_basemap)
        self.assertEqual(attribution, "OpenTopoMap")
        self.assertEqual(zoom, 8)
        self.assertTrue(
            any("provider yang sama" in message for message, _, _ in progress_events)
        )

    def test_raster_basemap_reports_complete_tile_progress(self) -> None:
        tile_buffer = BytesIO()
        Image.new("RGB", (256, 256), "#b9d7e8").save(tile_buffer, format="PNG")
        progress_events: list[tuple[str, int, int]] = []
        with TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "utils.publication_map._basemap_cache_directory",
                    return_value=Path(temp_dir),
                ),
                patch(
                    "utils.publication_map._fetch_tile_from_templates",
                    return_value=tile_buffer.getvalue(),
                ),
            ):
                image, _, _ = _render_raster_basemap(
                    (106.0, -7.0, 108.0, -5.0),
                    (320, 240),
                    BASEMAP_OPENTOPO,
                    progress_callback=lambda message, completed, total: progress_events.append(
                        (message, completed, total)
                    ),
                )

        self.assertEqual(image.size, (320, 240))
        self.assertGreater(len(progress_events), 1)
        _, completed, total = progress_events[-1]
        self.assertGreater(total, 0)
        self.assertEqual(completed, total)

    def test_uniform_103_byte_provider_tile_is_valid_and_keeps_its_color(self) -> None:
        payload = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEAAQMAAABmvDolAAAAA1BMVEWQzMsx"
            "kAZQAAAAH0lEQVRoge3BAQ0AAADCoPdPbQ43oAAAAAAAAAAAvg0hAAABmmDh1QAA"
            "AABJRU5ErkJggg=="
        )

        self.assertEqual(len(payload), 103)
        self.assertTrue(_valid_tile_bytes(payload))
        tile = _decode_raster_tile(payload)
        self.assertEqual(tile.mode, "RGB")
        self.assertEqual(tile.size, (256, 256))
        self.assertEqual(tile.getpixel((128, 128)), (144, 204, 203))

    def test_raster_tile_validator_rejects_wrong_dimensions(self) -> None:
        buffer = BytesIO()
        Image.new("RGB", (1, 1), "#90cccb").save(buffer, format="PNG")
        self.assertFalse(_valid_tile_bytes(buffer.getvalue()))

    def test_raster_logical_cache_uses_one_rotated_endpoint_then_skips_network(self) -> None:
        tile_buffer = BytesIO()
        Image.new("RGB", (256, 256), "#b9d7e8").save(tile_buffer, format="PNG")
        payload = tile_buffer.getvalue()
        templates = (
            "https://endpoint-a.example/{z}/{x}/{y}.png",
            "https://endpoint-b.example/{z}/{x}/{y}.png",
        )

        with TemporaryDirectory() as temp_dir:
            with patch(
                "utils.publication_map._basemap_cache_directory",
                return_value=Path(temp_dir),
            ):
                with patch(
                    "utils.publication_map._fetch_tile",
                    return_value=payload,
                ) as first_fetch:
                    first = _fetch_tile_from_templates(
                        templates,
                        zoom=8,
                        tile_x=201,
                        tile_y=130,
                    )

                logical_cache = _logical_raster_cache_path(
                    templates,
                    zoom=8,
                    tile_x=201,
                    tile_y=130,
                )
                first_fetch.assert_called_once_with(
                    "https://endpoint-b.example/8/201/130.png"
                )
                self.assertTrue(logical_cache.is_file())
                self.assertTrue(_valid_tile_bytes(logical_cache.read_bytes()))
                self.assertEqual(first, payload)

                with patch("utils.publication_map._fetch_tile") as second_fetch:
                    second = _fetch_tile_from_templates(
                        templates,
                        zoom=8,
                        tile_x=201,
                        tile_y=130,
                    )

                second_fetch.assert_not_called()
                self.assertEqual(second, payload)

    def test_raster_tile_failure_does_not_try_other_subdomains_in_same_round(self) -> None:
        templates = (
            "https://a.example/{z}/{x}/{y}.png",
            "https://b.example/{z}/{x}/{y}.png",
            "https://c.example/{z}/{x}/{y}.png",
        )
        with TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "utils.publication_map._basemap_cache_directory",
                    return_value=Path(temp_dir),
                ),
                patch(
                    "utils.publication_map._fetch_tile",
                    side_effect=PublicationMapError("rate limited"),
                ) as fetch,
            ):
                with self.assertRaises(PublicationMapError):
                    _fetch_tile_from_templates(
                        templates,
                        zoom=8,
                        tile_x=201,
                        tile_y=130,
                    )
        self.assertEqual(fetch.call_count, 1)

    def test_raster_tile_honors_retry_after_header(self) -> None:
        error = urllib.error.HTTPError(
            "https://a.example/8/201/130.png",
            429,
            "Too Many Requests",
            {"Retry-After": "7"},
            None,
        )
        _download_raster_tile.cache_clear()
        try:
            with patch("utils.publication_map.urllib.request.urlopen", side_effect=error):
                with self.assertRaises(TileRetryAfterError) as raised:
                    _download_raster_tile("https://a.example/8/201/130.png")
            self.assertEqual(raised.exception.retry_after_seconds, 7.0)
        finally:
            _download_raster_tile.cache_clear()

    def test_rate_limited_tile_observes_its_own_cooldown(self) -> None:
        templates = ("https://a.example/{z}/{x}/{y}.png",)
        _TILE_RETRY_NOT_BEFORE.clear()
        try:
            with TemporaryDirectory() as temp_dir:
                with (
                    patch(
                        "utils.publication_map._basemap_cache_directory",
                        return_value=Path(temp_dir),
                    ),
                    patch(
                        "utils.publication_map._fetch_tile",
                        side_effect=TileRetryAfterError("tunggu", 7),
                    ) as first_fetch,
                ):
                    with self.assertRaises(TileRetryAfterError):
                        _fetch_tile_from_templates(
                            templates, zoom=8, tile_x=201, tile_y=130
                        )
                    first_fetch.assert_called_once()

                    with patch("utils.publication_map._fetch_tile") as second_fetch:
                        with self.assertRaises(TileRetryAfterError):
                            _fetch_tile_from_templates(
                                templates, zoom=8, tile_x=201, tile_y=130
                            )
                    second_fetch.assert_not_called()
        finally:
            _TILE_RETRY_NOT_BEFORE.clear()

    def test_publication_zoom_caps_tile_requests(self) -> None:
        bounds = (94.0, -11.5, 141.5, 6.5)
        zoom = _choose_zoom(bounds, 1000, 700)
        left, top = _mercator_pixel(bounds[0], bounds[3], zoom)
        right, bottom = _mercator_pixel(bounds[2], bounds[1], zoom)
        tile_count = (
            int(math.floor((right - 1e-9) / 256))
            - int(math.floor(left / 256))
            + 1
        ) * (
            int(math.floor((bottom - 1e-9) / 256))
            - int(math.floor(top / 256))
            + 1
        )
        self.assertLessEqual(tile_count, MAX_PUBLICATION_TILE_COUNT)
        self.assertEqual(TILE_DOWNLOAD_TIMEOUT_SECONDS, 8)

    def test_partial_raster_round_resumes_from_exact_cached_count(self) -> None:
        tile_buffer = BytesIO()
        Image.new("RGB", (256, 256), "#b9d7e8").save(tile_buffer, format="PNG")
        payload = tile_buffer.getvalue()
        failed_once = {"value": False}

        def partially_fetch(
            templates: tuple[str, ...],
            *,
            zoom: int,
            tile_x: int,
            tile_y: int,
        ) -> bytes:
            if not failed_once["value"]:
                failed_once["value"] = True
                raise PublicationMapError("gangguan tile uji")
            return _write_cached_tile(
                _logical_raster_cache_path(
                    templates,
                    zoom=zoom,
                    tile_x=tile_x,
                    tile_y=tile_y,
                ),
                payload,
                _valid_tile_bytes,
                label="tile uji",
            )

        def finish_fetch(
            templates: tuple[str, ...],
            *,
            zoom: int,
            tile_x: int,
            tile_y: int,
        ) -> bytes:
            return _write_cached_tile(
                _logical_raster_cache_path(
                    templates,
                    zoom=zoom,
                    tile_x=tile_x,
                    tile_y=tile_y,
                ),
                payload,
                _valid_tile_bytes,
                label="tile uji",
            )

        with TemporaryDirectory() as temp_dir:
            with patch(
                "utils.publication_map._basemap_cache_directory",
                return_value=Path(temp_dir),
            ):
                first_events: list[tuple[str, int, int]] = []
                with patch(
                    "utils.publication_map._fetch_tile_from_templates",
                    side_effect=partially_fetch,
                ):
                    with self.assertRaises(PublicationMapError):
                        _render_raster_basemap(
                            (106.0, -7.0, 108.0, -5.0),
                            (320, 240),
                            BASEMAP_OPENTOPO,
                            progress_callback=lambda message, completed, total: first_events.append(
                                (message, completed, total)
                            ),
                        )

                first_completed = first_events[-1][1]
                total_tiles = first_events[-1][2]
                self.assertEqual(first_completed, total_tiles - 1)

                second_events: list[tuple[str, int, int]] = []
                with patch(
                    "utils.publication_map._fetch_tile_from_templates",
                    side_effect=finish_fetch,
                ) as second_fetch:
                    image, _, _ = _render_raster_basemap(
                        (106.0, -7.0, 108.0, -5.0),
                        (320, 240),
                        BASEMAP_OPENTOPO,
                        progress_callback=lambda message, completed, total: second_events.append(
                            (message, completed, total)
                        ),
                    )

                self.assertEqual(second_events[0][1], first_completed)
                self.assertIn("tile tersimpan", second_events[0][0])
                self.assertEqual(second_fetch.call_count, 1)
                self.assertEqual(second_events[-1][1], total_tiles)
                self.assertEqual(image.size, (320, 240))

    def test_every_basemap_option_can_produce_a_browser_free_snapshot(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "tanggal": ["2026-01-01"],
                "jam": ["12:00:00"],
                "lintang": [-6.2],
                "bujur": [106.8],
                "kedalaman": [10.0],
                "magnitudo": [4.0],
            }
        )
        empty = {"type": "FeatureCollection", "features": []}
        fake_basemap = Image.new("RGB", (320, 240), "#b9d7e8")
        with patch(
            "utils.publication_map._render_basemap",
            return_value=(fake_basemap, "Uji basemap", 8),
        ):
            for basemap_name in (
                BASEMAP_OPENTOPO,
                BASEMAP_RELIEF,
                BASEMAP_CYCLOSM,
                BASEMAP_HUMANITARIAN,
                BASEMAP_SHORTBREAD,
            ):
                with self.subTest(basemap_name=basemap_name):
                    snapshot = capture_filtered_map(
                        earthquake_df,
                        empty,
                        empty,
                        output_size=(320, 240),
                        padding_percent=12,
                        marker_scale_percent=100,
                        show_basemap=True,
                        basemap_name=basemap_name,
                        show_faults=False,
                        show_trenches=False,
                        show_provinces=False,
                    )
                    with Image.open(BytesIO(snapshot)) as rendered:
                        self.assertEqual(rendered.size, (320, 240))

    def test_region_labels_font_size_can_scale(self) -> None:
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        rendered_map = build_indonesia_map(
            province_geojson=province_geojson,
            show_region_labels=True,
            region_label_font_px=22,
            show_earthquake_legend=False,
        ).get_root().render()

        self.assertIn("700 22px", rendered_map)
        self.assertIn("Nama Provinsi", rendered_map)

    def test_shortbread_roads_can_be_enabled(self) -> None:
        rendered_map = build_indonesia_map(
            basemap_name=BASEMAP_SHORTBREAD,
            show_shortbread_roads=True,
        ).get_root().render()

        self.assertIn("var major", rendered_map)
        self.assertIn("street_polygons: {fill: true", rendered_map)

    def test_shortbread_rivers_can_be_hidden(self) -> None:
        rendered_map = build_indonesia_map(
            basemap_name=BASEMAP_SHORTBREAD,
            show_shortbread_rivers=False,
        ).get_root().render()

        self.assertIn("properties.kind === 'river'", rendered_map)
        self.assertIn("var naturalWaterway", rendered_map)

    def test_topographic_basemap_can_be_hidden(self) -> None:
        rendered_map = build_indonesia_map(
            show_basemap=False,
        ).get_root().render()

        self.assertNotIn("tile.opentopomap.org", rendered_map)

    def test_earthquake_layer_and_legend_can_be_hidden(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-6.2],
                "bujur": [106.8],
                "kedalaman": [20.0],
                "magnitudo": [5.0],
                "tanggal": ["2026-01-01"],
                "jam": ["12:00:00"],
            }
        )

        rendered_map = build_indonesia_map(
            earthquake_df,
            show_earthquakes=False,
        ).get_root().render()

        self.assertNotIn("Detail Gempa", rendered_map)
        self.assertNotIn("Legenda Gempa", rendered_map)

    def test_map_capture_controls_can_hide_leaflet_ui(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-6.2],
                "bujur": [106.8],
                "kedalaman": [20.0],
                "magnitudo": [5.0],
                "tanggal": ["2026-01-01"],
                "jam": ["12:00:00"],
            }
        )

        rendered_map = build_indonesia_map(
            earthquake_df,
            show_earthquake_legend=False,
            show_layer_control=False,
            show_map_scale=False,
            show_distance_measure=False,
        ).get_root().render()

        self.assertNotIn("Legenda Gempa", rendered_map)
        self.assertNotIn("L.control.layers", rendered_map)
        self.assertNotIn("L.control.scale", rendered_map)
        self.assertNotIn("DistanceMeasureControl", rendered_map)

    def test_distance_measure_control_renders_drag_behavior(self) -> None:
        rendered_map = build_indonesia_map(
            show_earthquake_legend=False,
        ).get_root().render()

        self.assertIn("DistanceMeasureControl", rendered_map)
        self.assertIn('options: {position: "topright"}', rendered_map)
        self.assertIn('aria-label", "Ukur jarak', rendered_map)
        self.assertIn('addEventListener("pointerdown"', rendered_map)
        self.assertIn('addEventListener("pointermove"', rendered_map)
        self.assertIn('addEventListener("pointerup"', rendered_map)
        self.assertIn("pixelDistance < 5", rendered_map)
        self.assertIn("map.distance(startLatLng, endLatLng) / 1000", rendered_map)
        self.assertIn('minimumFractionDigits: 2', rendered_map)
        self.assertIn('<strong>Jarak:</strong> ', rendered_map)
        self.assertIn("map.dragging.disable()", rendered_map)
        self.assertIn("map.dragging.enable()", rendered_map)
        self.assertIn("clearMeasurement();", rendered_map)
        self.assertIn('map.on("popupclose", function (event)', rendered_map)
        self.assertIn("event.popup === distancePopup", rendered_map)
        self.assertIn("const popupToRemove = distancePopup", rendered_map)
        self.assertIn("suppressNextMapClick = true", rendered_map)
        self.assertIn('addEventListener("click", onMapClick, true)', rendered_map)
        self.assertIn("placeBelowLayerControl", rendered_map)

    def test_distance_measure_control_can_be_hidden(self) -> None:
        rendered_map = build_indonesia_map(
            show_earthquake_legend=False,
            show_distance_measure=False,
        ).get_root().render()

        self.assertNotIn("DistanceMeasureControl", rendered_map)
        self.assertNotIn("distance-measure-control", rendered_map)

    def test_bundled_seismic_station_snapshot_loads_complete_dataset(self) -> None:
        records = load_seismic_stations(STATION_DATA_PATH)

        self.assertEqual(len(records), 452)
        self.assertEqual(records[0][2:5], ("AAFM", "IA", "Station Type MR, Alor Selatan, Alor, NTT"))
        self.assertEqual(sum(record[2] == "BBSI" for record in records), 2)
        self.assertEqual(sum(record[2] == "R02C2" for record in records), 1)
        self.assertNotIn("NMT07", {record[2] for record in records})

    def test_seismic_station_loader_skips_invalid_and_out_of_bounds_rows(self) -> None:
        with TemporaryDirectory() as temp_dir:
            station_path = Path(temp_dir) / "stations.txt"
            station_path.write_text(
                "#Network|Station|Latitude|Longitude|Elevation|SiteName|StartTime|EndTime\n"
                "IA|OK01|-6.2|106.8|25|Lokasi Uji|2020-01-01T00:00:00|\n"
                "IA|BAD01|bukan-angka|106.8||||\n"
                "IA|FAR01|-8.5|166.0||||\n",
                encoding="utf-8",
            )

            records = load_seismic_stations(station_path)

        self.assertEqual(
            records,
            [(-6.2, 106.8, "OK01", "IA", "Lokasi Uji", "25", "2020-01-01T00:00:00", "")],
        )

    def test_seismic_station_canvas_renders_clickable_popup_metadata(self) -> None:
        station_records = [
            (-6.2, 106.8, "TEST", "IA", "Pos Uji", "25", "2020-01-01", "2025-01-01"),
        ]
        visible_map = build_indonesia_map(
            show_stations=True,
            station_records=station_records,
            show_earthquake_legend=False,
        ).get_root().render()
        hidden_map = build_indonesia_map(
            show_stations=False,
            station_records=station_records,
            show_earthquake_legend=False,
        ).get_root().render()

        self.assertIn("StationCanvasLayer", visible_map)
        self.assertIn("station-canvas-layer", visible_map)
        self.assertIn('map.on("click", this._onClick, this)', visible_map)
        self.assertIn("Stasiun Seismik", visible_map)
        self.assertIn('optionalLine("Nama: ", record[4])', visible_map)
        self.assertIn('optionalLine("Elevasi: ", record[5] ? record[5] + " m" : "")', visible_map)
        self.assertIn('optionalLine("Mulai: ", record[6])', visible_map)
        self.assertIn('optionalLine("Selesai: ", record[7])', visible_map)
        self.assertIn('[-6.2,106.8,"TEST","IA","Pos Uji","25","2020-01-01","2025-01-01"]', visible_map)
        self.assertIn("StationCanvasLayer", hidden_map)
        self.assertIn('"Stasiun Seismik" : feature_group_', visible_map)
        self.assertIn('"Stasiun Seismik" : feature_group_', hidden_map)
        self.assertGreater(visible_map.count(".addTo(map_"), hidden_map.count(".addTo(map_"))

    def test_complete_station_layer_keeps_canvas_payload_compact(self) -> None:
        common_options = {
            "show_basemap": False,
            "show_earthquake_legend": False,
            "show_layer_control": False,
        }
        rendered_map = build_indonesia_map(
            show_stations=True,
            station_records=load_seismic_stations(STATION_DATA_PATH),
            **common_options,
        ).get_root().render()
        hidden_map = build_indonesia_map(
            show_stations=False,
            station_records=load_seismic_stations(STATION_DATA_PATH),
            **common_options,
        ).get_root().render()

        self.assertIn("StationCanvasLayer", rendered_map)
        self.assertIn("StationCanvasLayer", hidden_map)
        self.assertEqual(rendered_map.count("L.marker("), hidden_map.count("L.marker("))
        self.assertLess(len(rendered_map), 500_000)

    def test_location_description_is_rendered_in_map_popup(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-8.1],
                "bujur": [107.32],
                "kedalaman": [13.0],
                "magnitudo": [2.3],
                "tanggal": ["2025-10-01"],
                "jam": ["00:42:32"],
                "jenis_lokasi": ["Laut"],
                "jarak_darat_km": [10.0],
                "keterangan_lokasi": ["116 km BaratDaya KAB-GARUT-JABAR"],
                "dirasakan": ["Dirasakan di Garut II MMI"],
            }
        )

        rendered_map = build_indonesia_map(earthquake_df).get_root().render()

        self.assertIn("Keterangan lokasi:", rendered_map)
        self.assertIn("116 km BaratDaya KAB-GARUT-JABAR", rendered_map)
        self.assertIn("Dirasakan di Garut II MMI", rendered_map)

    def test_fault_popup_describes_distance_as_geometric_not_causal(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-6.2],
                "bujur": [106.8],
                "kedalaman": [12.0],
                "magnitudo": [4.5],
                "tanggal": ["2026-01-01"],
                "jam": ["12:00:00"],
                "sesar": ["Sesar Uji"],
                "jarak_sesar_km": [8.24],
            }
        )

        rendered_map = build_indonesia_map(earthquake_df).get_root().render()

        self.assertIn("Jejak sesar terdekat (geometris)", rendered_map)
        self.assertIn("Jarak horizontal episenter-jejak", rendered_map)
        self.assertIn("toFixed(1)", rendered_map)

    def test_large_earthquake_map_uses_compact_canvas_payload(self) -> None:
        source_df = pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        earthquake_df = pd.concat([source_df] * 834, ignore_index=True).iloc[:5000]

        rendered_map = build_indonesia_map(
            earthquake_df,
            show_basemap=False,
            show_faults=False,
            show_trenches=False,
            show_region_labels=False,
            show_layer_control=False,
        ).get_root().render()

        self.assertIn("EarthquakeWebGLLayer", rendered_map)
        self.assertIn("earthquake-webgl-layer", rendered_map)
        self.assertIn("gl.drawArrays(gl.POINTS", rendered_map)
        self.assertIn('].join("\\n");', rendered_map)
        self.assertIn('map.getContainer().appendChild(this._canvas)', rendered_map)
        self.assertIn('map.on("move zoom resize", this._scheduleReset, this)', rendered_map)
        self.assertIn("origin.x - panePosition.x", rendered_map)
        self.assertIn('pane: "earthquakePopupPane"', rendered_map)
        self.assertIn("latLngToContainerPoint(this._latlng)", rendered_map)
        self.assertIn("Math.ceil(Number(record[2])) + 6", rendered_map)
        self.assertIn('"preferCanvas": false', rendered_map)
        self.assertEqual(rendered_map.count("L.circleMarker"), 1)
        self.assertLess(len(rendered_map), 1_200_000)

    def test_manual_earthquake_visibility_uses_stable_event_ids(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-6.2, -6.3],
                "bujur": [106.8, 106.9],
                "kedalaman": [20.0, 35.0],
                "magnitudo": [5.0, 4.2],
                "tanggal": ["2026-01-01", "2026-01-02"],
                "jam": ["12:00:00", "13:00:00"],
            }
        )

        first_event_id = get_earthquake_event_id(earthquake_df.iloc[0])
        self.assertEqual(
            first_event_id,
            get_earthquake_event_id(earthquake_df.copy().iloc[0]),
        )
        self.assertNotEqual(
            first_event_id,
            get_earthquake_event_id(earthquake_df.iloc[1]),
        )

        visible_df = filter_hidden_earthquakes(
            earthquake_df,
            [first_event_id],
        )
        self.assertEqual(len(visible_df), 1)
        self.assertEqual(visible_df.iloc[0]["magnitudo"], 4.2)
        self.assertEqual(len(earthquake_df), 2)

    def test_earthquake_canvas_can_be_added_as_dynamic_feature_group(self) -> None:
        earthquake_df = pd.DataFrame(
            {
                "lintang": [-6.2],
                "bujur": [106.8],
                "kedalaman": [20.0],
                "magnitudo": [5.0],
                "tanggal": ["2026-01-01"],
                "jam": ["12:00:00"],
            }
        )
        map_shell = build_indonesia_map(
            earthquake_df,
            show_basemap=False,
            show_faults=False,
            show_trenches=False,
            show_region_labels=False,
            show_layer_control=False,
            embed_earthquake_layer=False,
        )

        shell_html = map_shell.get_root().render()
        build_earthquake_feature_group(earthquake_df).add_to(map_shell)
        dynamic_html = map_shell.get_root().render()

        self.assertNotIn("EarthquakeWebGLLayer", shell_html)
        self.assertIn("EarthquakeWebGLLayer", dynamic_html)
        self.assertIn("earthquake-webgl-layer", dynamic_html)
        self.assertIn('getContext("webgl", {', dynamic_html)
        self.assertIn("this._selectionMarker", dynamic_html)
        self.assertIn("earthquake-visibility-toggle", dynamic_html)
        self.assertIn('const actionToken = "hide:" + record[16]', dynamic_html)
        self.assertNotIn("panel di kanan peta", dynamic_html)
        self.assertIn('.fire("click", {latlng: selectedLatLng})', dynamic_html)

    def test_publication_map_renders_png_and_pdf_without_basemap(self) -> None:
        earthquake_df = clean_earthquake_data(
            pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        )
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)

        logo_buffer = BytesIO()
        Image.new("RGBA", (120, 80), "#0f766e").save(logo_buffer, format="PNG")
        second_logo_buffer = BytesIO()
        Image.new("RGBA", (100, 100), "#1d4ed8").save(
            second_logo_buffer,
            format="PNG",
        )
        png_bytes, pdf_bytes = render_publication_map(
            earthquake_df,
            fault_geojson,
            province_geojson,
            title="Peta Gempa Uji\nWilayah Jawa Barat",
            institution_name="Instansi Uji\nUnit Geofisika",
            logo_bytes=logo_buffer.getvalue(),
            second_logo_bytes=second_logo_buffer.getvalue(),
            logo_scale_percent=60,
            show_basemap=False,
            page_size="A4 Lanskap",
        )
        rendered_image = Image.open(BytesIO(png_bytes))

        self.assertEqual(rendered_image.size, (1684, 1191))
        self.assertEqual(rendered_image.format, "PNG")
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_publication_map_renders_print_resolution_at_300_dpi(self) -> None:
        earthquake_df = clean_earthquake_data(
            pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        )
        png_bytes, pdf_bytes = render_publication_map(
            earthquake_df,
            {"type": "FeatureCollection", "features": []},
            load_region_geojson(PROVINCE_DATA_PATH),
            title="Peta Gempa 300 DPI",
            institution_name="Instansi Uji",
            page_size="A4 Lanskap",
            output_dpi=300.0,
            show_basemap=False,
            show_faults=False,
            show_trenches=False,
            show_provinces=False,
        )

        self.assertEqual(Image.open(BytesIO(png_bytes)).size, (3508, 2481))
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_publication_map_rejects_empty_filtered_data(self) -> None:
        with self.assertRaises(PublicationMapError):
            render_publication_map(
                pd.DataFrame(),
                {"type": "FeatureCollection", "features": []},
                {"type": "FeatureCollection", "features": []},
                title="Peta Kosong",
                institution_name="Instansi Uji",
                show_basemap=False,
            )

    def test_publication_map_accepts_direct_map_snapshot(self) -> None:
        earthquake_df = clean_earthquake_data(
            pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        )
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        snapshot_buffer = BytesIO()
        Image.new("RGB", (1077, 965), "#4f8fb8").save(
            snapshot_buffer,
            format="PNG",
        )

        png_bytes, _ = render_publication_map(
            earthquake_df,
            fault_geojson,
            province_geojson,
            title="Peta Snapshot",
            institution_name="Instansi Uji",
            show_basemap=True,
            map_snapshot_bytes=snapshot_buffer.getvalue(),
        )
        rendered_image = Image.open(BytesIO(png_bytes)).convert("RGB")

        self.assertEqual(rendered_image.getpixel((500, 500)), (79, 143, 184))

    def test_publication_map_accepts_shortbread_legend(self) -> None:
        earthquake_df = clean_earthquake_data(
            pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        )
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        snapshot_buffer = BytesIO()
        Image.new("RGB", (1077, 965), "#b9ddea").save(
            snapshot_buffer,
            format="PNG",
        )

        png_bytes, pdf_bytes = render_publication_map(
            earthquake_df,
            fault_geojson,
            province_geojson,
            title="Peta Shortbread",
            institution_name="Instansi Uji",
            basemap_name=BASEMAP_SHORTBREAD,
            show_shortbread_roads=False,
            show_shortbread_topography=True,
            show_basemap_legend=True,
            map_snapshot_bytes=snapshot_buffer.getvalue(),
        )

        self.assertEqual(Image.open(BytesIO(png_bytes)).size, (1684, 1191))
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

    def test_publication_map_can_show_population_density_legend(self) -> None:
        earthquake_df = clean_earthquake_data(
            pd.read_csv(SAMPLE_DIR / "gempa_sample_valid.csv")
        )
        fault_geojson = load_fault_geojson(FAULT_DATA_PATH)
        province_geojson = load_region_geojson(PROVINCE_DATA_PATH)
        snapshot_buffer = BytesIO()
        Image.new("RGB", (1077, 965), "#ffffff").save(
            snapshot_buffer,
            format="PNG",
        )
        density_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"kepadatan_penduduk_per_km2": 12_000},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.0, -7.0],
                                [107.0, -7.0],
                                [107.0, -6.0],
                                [106.0, -6.0],
                                [106.0, -7.0],
                            ]
                        ],
                    },
                }
            ],
        }

        visible_png, _ = render_publication_map(
            earthquake_df,
            fault_geojson,
            province_geojson,
            population_density_geojson=density_geojson,
            title="Peta Kepadatan",
            institution_name="Instansi Uji",
            show_basemap=False,
            show_faults=False,
            show_trenches=False,
            show_provinces=False,
            show_inset=False,
            show_population_density=True,
            show_population_density_legend=True,
            map_snapshot_bytes=snapshot_buffer.getvalue(),
        )
        hidden_png, _ = render_publication_map(
            earthquake_df,
            fault_geojson,
            province_geojson,
            population_density_geojson=density_geojson,
            title="Peta Kepadatan",
            institution_name="Instansi Uji",
            show_basemap=False,
            show_faults=False,
            show_trenches=False,
            show_provinces=False,
            show_inset=False,
            show_population_density=True,
            show_population_density_legend=False,
            map_snapshot_bytes=snapshot_buffer.getvalue(),
        )

        blue = ImageColor.getrgb(POPULATION_DENSITY_COLORS[-1])
        self.assertIn(blue, set(Image.open(BytesIO(visible_png)).getdata()))
        self.assertNotIn(blue, set(Image.open(BytesIO(hidden_png)).getdata()))

    def test_district_density_overlay_survives_focus_mask_on_every_basemap(self) -> None:
        empty_earthquakes = pd.DataFrame(
            columns=["bujur", "lintang", "kedalaman", "magnitudo"]
        )
        empty_features = {"type": "FeatureCollection", "features": []}
        district_geometry = {
            "type": "Polygon",
            "coordinates": [[
                [106.0, -7.0],
                [107.0, -7.0],
                [107.0, -6.0],
                [106.0, -6.0],
                [106.0, -7.0],
            ]],
        }
        district_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"WADMPR": "Provinsi Uji", "WADMKK": "Kabupaten A"},
                "geometry": district_geometry,
            }],
        }
        province_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"PROVINSI": "Provinsi Uji"},
                "geometry": district_geometry,
            }],
        }
        density_geojson = {
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {
                    "kabupaten_kota": "Kabupaten A",
                    "kecamatan": "Kecamatan A",
                    "kepadatan_penduduk_per_km2": 12_000,
                },
                "geometry": district_geometry,
            }],
        }

        def fake_basemap(
            _bounds: tuple[float, float, float, float],
            size: tuple[int, int],
            basemap_name: str,
            **_kwargs: object,
        ) -> tuple[Image.Image, str, int]:
            return Image.new("RGB", size, "#dbeafe"), basemap_name, 8

        common = dict(
            df=empty_earthquakes,
            fault_geojson=empty_features,
            province_geojson=province_geojson,
            district_geojson=district_geojson,
            population_density_geojson=density_geojson,
            output_size=(320, 240),
            padding_percent=12,
            marker_scale_percent=100,
            show_basemap=True,
            show_faults=False,
            show_trenches=False,
            show_provinces=False,
            show_region_labels=False,
            district_names=["Kabupaten A"],
        )
        basemaps = (
            BASEMAP_OPENTOPO,
            BASEMAP_RELIEF,
            BASEMAP_CYCLOSM,
            BASEMAP_HUMANITARIAN,
            BASEMAP_SHORTBREAD,
        )
        with patch("utils.publication_map._render_basemap", side_effect=fake_basemap):
            for basemap_name in basemaps:
                without_density = Image.open(BytesIO(capture_filtered_map(
                    basemap_name=basemap_name,
                    show_population_density=False,
                    **common,
                ))).convert("RGB")
                with_density = Image.open(BytesIO(capture_filtered_map(
                    basemap_name=basemap_name,
                    show_population_density=True,
                    **common,
                ))).convert("RGB")
                difference = ImageChops.difference(without_density, with_density)
                self.assertIsNotNone(difference.getbbox(), basemap_name)
                changed_pixels = sum(
                    pixel != (0, 0, 0) for pixel in difference.getdata()
                )
                self.assertGreater(changed_pixels, 1_000, basemap_name)

    def test_relief_map_labels_and_highlights_selected_district(self) -> None:
        district_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"WADMPR": "Provinsi Uji", "WADMKK": "Kabupaten A"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [106.0, -7.0], [107.0, -7.0], [107.0, -6.0],
                            [106.0, -6.0], [106.0, -7.0],
                        ]],
                    },
                },
                {
                    "type": "Feature",
                    "properties": {"WADMPR": "Provinsi Uji", "WADMKK": "Kabupaten B"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [107.0, -7.0], [108.0, -7.0], [108.0, -6.0],
                            [107.0, -6.0], [107.0, -7.0],
                        ]],
                    },
                },
            ],
        }
        html = build_indonesia_map(
            district_geojson=district_geojson,
            district_names=["Kabupaten A"],
            province_names=["Provinsi Uji"],
            basemap_name=BASEMAP_RELIEF,
            show_region_labels=True,
        ).get_root().render()

        self.assertIn("Fokus Kabupaten/Kota", html)
        self.assertIn("Nama Kabupaten/Kota", html)
        self.assertIn("district-label-selected", html)
        self.assertIn("color:#000000", html)
        self.assertIn("Kabupaten A", html)
        self.assertIn("Kabupaten B", html)
        self.assertIn("districtFocusPane.style.zIndex = 620", html)

    def test_region_name_overlay_can_be_enabled_or_disabled(self) -> None:
        province_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"PROVINSI": "Provinsi Uji"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [106.0, -7.0],
                                [107.0, -7.0],
                                [107.0, -6.0],
                                [106.0, -6.0],
                                [106.0, -7.0],
                            ]
                        ],
                    },
                }
            ],
        }

        labels_on = build_indonesia_map(
            province_geojson=province_geojson,
            show_region_labels=True,
        ).get_root().render()
        labels_off = build_indonesia_map(
            province_geojson=province_geojson,
            show_region_labels=False,
        ).get_root().render()

        self.assertIn("province-label", labels_on)
        self.assertIn("Provinsi Uji", labels_on)
        self.assertNotIn("province-label", labels_off)
        self.assertNotIn("Provinsi Uji", labels_off)


if __name__ == "__main__":
    unittest.main()
