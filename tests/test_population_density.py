from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from scripts.build_population_density_dataset import (
    MAX_IMPLIED_SUBDISTRICT_POPULATION,
    VALIDATION_AREA_OVERRIDES_KM2,
    validate_implied_population,
)
from scripts.scrape_bps_population_density import (
    _density_column_id,
    _parse_density,
)

from utils.map_builder import (
    POPULATION_DENSITY_COLORS,
    POPULATION_DENSITY_DISTRICT_LAYER_NAME,
    build_population_density_surfaces,
    build_indonesia_map,
    get_population_density_breaks,
    get_population_density_color,
    get_population_density_legend_labels,
)
from utils.population_density import (
    filter_population_density,
    filter_population_density_geojson,
    join_population_density_geojson,
    load_population_density_csv,
    load_subdistrict_geojson,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
DENSITY_PATH = ROOT_DIR / "data" / "kepadatan_penduduk_kecamatan.csv"
SOURCE_PATH = ROOT_DIR / "data" / "kepadatan_penduduk_sumber_bps.csv"
ORIGINAL_SUBDISTRICT_PATHS = (
    ROOT_DIR / "data" / "kecamatan_jawa_barat.geojson",
    ROOT_DIR / "data" / "kecamatan_banten.geojson",
    ROOT_DIR / "data" / "kecamatan_jawa_tengah.geojson",
    ROOT_DIR / "data" / "kecamatan_jawa_timur.geojson",
    ROOT_DIR / "data" / "kecamatan_dki_jakarta.geojson",
    ROOT_DIR / "data" / "kecamatan_yogyakarta.geojson",
    ROOT_DIR / "data" / "kecamatan_sumatera_barat.geojson",
    ROOT_DIR / "data" / "kecamatan_jambi.geojson",
    ROOT_DIR / "data" / "kecamatan_sumatera_selatan.geojson",
    ROOT_DIR / "data" / "kecamatan_bengkulu.geojson",
    ROOT_DIR / "data" / "kecamatan_lampung.geojson",
)
SUBDISTRICT_PATH = ROOT_DIR / "data" / "kecamatan_lightweight.geojson"
REGION_CSV_DIR = ROOT_DIR / "data" / "bps_kepadatan_per_kabupaten_kota"


class PopulationDensityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.density_df = load_population_density_csv(DENSITY_PATH)
        cls.subdistrict_geojson = load_subdistrict_geojson(SUBDISTRICT_PATH)

    def test_dataset_covers_every_subdistrict_of_both_provinces(self) -> None:
        self.assertEqual(len(self.density_df), 2985)
        self.assertEqual(self.density_df["nama_kabupaten_kota"].nunique(), 185)
        self.assertEqual(len(list(REGION_CSV_DIR.glob("*.csv"))), 185)
        self.assertEqual(
            self.density_df.groupby("provinsi")["nama_kecamatan"].count().to_dict(),
            {
                "Banten": 155,
                "Bengkulu": 117,  # 9 of 10; Lebong has no undamaged BPS table.
                # 5 of 6 DKI cities/regency; Kepulauan Seribu is not published
                # per kecamatan.
                "DKI Jakarta": 42,
                "Daerah Istimewa Yogyakarta": 78,
                # 10 of 11; Sarolangun has no undamaged BPS table.
                "Jambi": 133,
                "Jawa Barat": 627,
                "Jawa Tengah": 576,
                # 36 of 38 Jatim regencies/cities; Lamongan and Kota Mojokerto
                # have no undamaged BPS table.
                "Jawa Timur": 636,
                # 14 of 15; Lampung Tengah has no undamaged BPS table.
                "Lampung": 201,
                "Sumatera Barat": 179,  # 19 of 19.
                "Sumatera Selatan": 241,  # 17 of 17.
            },
        )
        # BPS mangles densities of 1000+ on some tables, collapsing them to
        # single digits. Cities are never genuinely sparse, so their densities
        # must stay well clear of that -- a city below 50 would be a mangled
        # value that slipped the scraper's audit. Sparse Sumatran regencies do
        # dip far lower for real (coastal swamp, transmigration land), so the
        # rest of the guard is only that no value is zero or negative.
        codes = self.density_df["kode_bps_kabupaten_kota"].astype(str)
        city_rows = self.density_df[codes.str[2] == "7"]
        self.assertGreaterEqual(
            city_rows["kepadatan_penduduk_per_km2"].min(), 50
        )
        self.assertGreater(self.density_df["kepadatan_penduduk_per_km2"].min(), 0)

        source_df = pd.read_csv(SOURCE_PATH, encoding="utf-8-sig")
        self.assertEqual(len(source_df), 185)
        self.assertEqual(
            source_df["jumlah_kecamatan"].sum(),
            len(self.density_df),
        )
        self.assertEqual(
            set(source_df["status_sumber"]),
            {"tabel_bps", "fallback_publikasi_pdf"},
        )

    def test_density_column_selection_does_not_depend_on_bps_column_order(self) -> None:
        columns = {
            "sex_ratio": {"nama_variabel": "Rasio Jenis Kelamin Penduduk"},
            "density": {
                "nama_variabel": "Kepadatan Penduduk per km persegi",
                "nama_variabel_en": "Population Density per sq.km",
            },
            "population": {"nama_variabel": "Jumlah Penduduk"},
        }

        self.assertEqual(_density_column_id(columns), "density")

    def test_region_specific_scale_repairs_bps_decimal_mangling(self) -> None:
        # Tebo publishes 41.93 as "4.193" and Mojokerto publishes 3872.80
        # as "387.280" in the affected payloads. Both need a 0.01 scale.
        self.assertAlmostEqual(_parse_density("4.193", scale=0.01), 41.93)
        self.assertAlmostEqual(_parse_density("387.280", scale=0.01), 3872.8)

        directory = json.loads(
            (ROOT_DIR / "scripts" / "bps_region_directory.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(directory["1509"]["density_scale_by_year"]["2025"], 0.01)
        self.assertEqual(directory["3516"]["density_scale_by_year"]["2024"], 0.01)

    def test_known_bps_decimal_mangling_rows_are_regenerated(self) -> None:
        keyed = self.density_df.set_index(
            ["kode_bps_kabupaten_kota", "nama_kecamatan"]
        )["kepadatan_penduduk_per_km2"]

        self.assertAlmostEqual(keyed.loc[(1509, "Tebo Ilir")], 41.93)
        self.assertAlmostEqual(keyed.loc[(3516, "Sooko")], 3872.8)
        # This official value is intentionally unchanged: BPS uses an 8.04
        # km2 inhabited-area denominator that excludes Nusakambangan.
        self.assertEqual(keyed.loc[(3301, "Cilacap Selatan")], 10607)

    def test_implied_population_validation_rejects_corrupt_density(self) -> None:
        boundary = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"WADMKK": "Example", "WADMKC": "Wide"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
                        ],
                    },
                }
            ],
        }
        rows = [
            {
                "nama_kabupaten_kota": "Kabupaten Example",
                "nama_kecamatan": "Wide",
                "kepadatan_penduduk_per_km2": 100,
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            boundary_path = Path(temp_dir) / "boundary.geojson"
            boundary_path.write_text(json.dumps(boundary), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "lebih dari"):
                validate_implied_population(rows, boundary_path)

    def test_regenerated_dataset_passes_implied_population_validation(self) -> None:
        implied = validate_implied_population(
            self.density_df.to_dict(orient="records"), SUBDISTRICT_PATH
        )

        self.assertEqual(len(implied), len(self.density_df))
        self.assertLessEqual(
            max(implied.values()), MAX_IMPLIED_SUBDISTRICT_POPULATION
        )
        self.assertAlmostEqual(
            implied[("cilacap", "cilacapselatan")],
            10607 * VALIDATION_AREA_OVERRIDES_KM2[("cilacap", "cilacapselatan")],
        )

    def test_all_bps_rows_join_to_big_subdistrict_boundaries(self) -> None:
        joined = join_population_density_geojson(
            self.subdistrict_geojson,
            self.density_df,
            require_complete=True,
        )

        # Two BIG polygons carry a duplicated kecamatan name (Agam "IV Koto",
        # Lima Puluh Kota "Buki Barisan" are each split into two features), so
        # the join emits two extra features over the 2985 audited rows.
        self.assertEqual(len(joined["features"]), 2987)
        first_properties = joined["features"][0]["properties"]
        self.assertIn("kepadatan_penduduk_per_km2", first_properties)
        self.assertEqual(
            set(first_properties),
            {
                "provinsi",
                "kabupaten_kota",
                "kecamatan",
                "tahun",
                "kepadatan_penduduk_per_km2",
            },
        )

    def test_runtime_boundaries_are_compact_and_complete(self) -> None:
        self.assertEqual(len(self.subdistrict_geojson["features"]), 3070)
        # The runtime copy carries only what the join reads, so it stays
        # smaller than the province snapshots it is built from.
        self.assertLess(
            SUBDISTRICT_PATH.stat().st_size,
            sum(path.stat().st_size for path in ORIGINAL_SUBDISTRICT_PATHS),
        )
        self.assertEqual(
            {
                key
                for feature in self.subdistrict_geojson["features"]
                for key in feature["properties"]
            },
            {"WADMKK", "WADMKC"},
        )

    def test_region_and_density_filters_can_be_combined(self) -> None:
        filtered = filter_population_density(
            self.density_df,
            ["Kota Bandung"],
            (20_000, 50_000),
        )

        self.assertFalse(filtered.empty)
        self.assertEqual(set(filtered["nama_kabupaten_kota"]), {"Kota Bandung"})
        self.assertTrue(
            filtered["kepadatan_penduduk_per_km2"].between(20_000, 50_000).all()
        )

        all_joined = join_population_density_geojson(
            self.subdistrict_geojson,
            self.density_df,
            require_complete=True,
        )
        filtered_geojson = filter_population_density_geojson(
            all_joined,
            ["Kota Bandung"],
            (20_000, 50_000),
        )
        self.assertEqual(
            len(filtered_geojson["features"]),
            len(filtered),
        )

    def test_bare_regency_filter_matches_bps_name_without_mixing_city(self) -> None:
        filtered = filter_population_density(
            self.density_df,
            ["Bandung"],
            province_names=["Jawa Barat"],
        )

        self.assertFalse(filtered.empty)
        self.assertEqual(
            set(filtered["nama_kabupaten_kota"]),
            {"Kabupaten Bandung"},
        )

        all_joined = join_population_density_geojson(
            self.subdistrict_geojson,
            self.density_df,
            require_complete=True,
        )
        filtered_geojson = filter_population_density_geojson(
            all_joined,
            ["Bandung"],
            province_names=["Jawa Barat"],
        )
        self.assertEqual(len(filtered_geojson["features"]), len(filtered))
        self.assertEqual(
            {
                feature["properties"]["kabupaten_kota"]
                for feature in filtered_geojson["features"]
            },
            {"Kabupaten Bandung"},
        )

    def test_density_colors_normalize_to_selected_province_and_districts(self) -> None:
        all_joined = join_population_density_geojson(
            self.subdistrict_geojson,
            self.density_df,
            require_complete=True,
        )
        banten = filter_population_density_geojson(
            all_joined,
            province_names=["Banten"],
        )
        self.assertTrue(banten["features"])
        self.assertEqual(
            {
                feature["properties"]["provinsi"]
                for feature in banten["features"]
            },
            {"Banten"},
        )

        selected_regions = sorted(
            self.density_df.loc[
                self.density_df["provinsi"].eq("Jawa Barat"),
                "nama_kabupaten_kota",
            ].unique()
        )[:3]
        selected = filter_population_density_geojson(
            all_joined,
            region_names=selected_regions,
            province_names=["Jawa Barat"],
        )
        selected_values = [
            feature["properties"]["kepadatan_penduduk_per_km2"]
            for feature in selected["features"]
        ]
        breaks = get_population_density_breaks(selected)

        self.assertEqual(
            {
                feature["properties"]["kabupaten_kota"]
                for feature in selected["features"]
            },
            set(selected_regions),
        )
        self.assertAlmostEqual(breaks[0], min(selected_values))
        self.assertAlmostEqual(breaks[-1], max(selected_values))
        self.assertEqual(
            get_population_density_color(min(selected_values), breaks),
            POPULATION_DENSITY_COLORS[0],
        )
        self.assertEqual(
            get_population_density_color(max(selected_values), breaks),
            POPULATION_DENSITY_COLORS[-1],
        )
        self.assertEqual(
            len(get_population_density_legend_labels(breaks)),
            len(POPULATION_DENSITY_COLORS),
        )

    def test_map_renders_interpolated_density_without_subdistrict_boundaries(self) -> None:
        bandung = filter_population_density(
            self.density_df,
            ["Kota Bandung"],
        )
        joined = join_population_density_geojson(
            self.subdistrict_geojson,
            bandung,
            require_complete=True,
        )

        html = build_indonesia_map(
            population_density_geojson=joined,
            show_population_density=True,
            show_basemap=False,
        ).get_root().render()

        self.assertIn("Kriging Kepadatan Penduduk (BPS)", html)
        self.assertIn("L.imageOverlay", html)
        self.assertIn("data:image/png;base64", html)
        self.assertNotIn("Bojongloa Kaler", html)
        self.assertNotIn('"weight": 0.75', html)
        self.assertIn("Legenda Kepadatan", html)
        self.assertIn("Kriging Provinsi", html)
        self.assertIn("Sumber data:", html)
        self.assertIn("Badan Pusat Statistik (BPS)", html)
        self.assertIn("https://www.bps.go.id/", html)
        self.assertIn("data 2024", html)
        self.assertIn('<details class="map-legend map-legend-density">', html)
        self.assertIn("image-rendering:auto!important", html)

    def test_district_filter_uses_subdistrict_choropleth_with_boundaries(self) -> None:
        bandung = filter_population_density(
            self.density_df,
            ["Kota Bandung"],
        )
        joined = join_population_density_geojson(
            self.subdistrict_geojson,
            bandung,
            require_complete=True,
        )

        html = build_indonesia_map(
            population_density_geojson=joined,
            district_names=["Kota Bandung"],
            show_population_density=True,
            show_basemap=False,
            include_geology_layer=False,
        ).get_root().render()

        self.assertIn(POPULATION_DENSITY_DISTRICT_LAYER_NAME, html)
        self.assertIn("Bojongloa Kaler", html)
        self.assertIn('"weight": 0.75', html)
        self.assertIn("Kecamatan", html)
        self.assertNotIn("L.imageOverlay", html)

    def test_map_uses_blue_density_palette_and_can_hide_leaflet_legend(self) -> None:
        bandung = filter_population_density(
            self.density_df,
            ["Kota Bandung"],
        )
        joined = join_population_density_geojson(
            self.subdistrict_geojson,
            bandung,
            require_complete=True,
        )

        with_legend = build_indonesia_map(
            population_density_geojson=joined,
            show_population_density=True,
            show_basemap=False,
        ).get_root().render()
        without_legend = build_indonesia_map(
            population_density_geojson=joined,
            show_population_density=True,
            show_population_density_legend=False,
            show_basemap=False,
        ).get_root().render()

        self.assertIn("#172554", with_legend)
        self.assertNotIn("#cc4c02", with_legend)
        self.assertIn("Legenda Kepadatan", with_legend)
        self.assertNotIn("Legenda Kepadatan", without_legend)
        self.assertIn(".map-legend-density{top:108px!important;}", with_legend)
        self.assertIn('document.querySelector(".map-legend-density")', with_legend)
        self.assertIn("rulerBounds.bottom + 8", with_legend)
        self.assertIn('window.addEventListener("load", placeBelowLayerControl', with_legend)

    def test_density_pane_is_explicitly_below_earthquake_pane(self) -> None:
        bandung = filter_population_density(self.density_df, ["Kota Bandung"])
        joined = join_population_density_geojson(
            self.subdistrict_geojson,
            bandung,
            require_complete=True,
        )
        earthquake_df = pd.DataFrame(
            [
                {
                    "tanggal": "2026-07-17",
                    "jam": "12:00:00",
                    "lintang": -6.91,
                    "bujur": 107.61,
                    "kedalaman": 20.0,
                    "magnitudo": 4.2,
                }
            ]
        )

        html = build_indonesia_map(
            earthquake_df,
            population_density_geojson=joined,
            show_population_density=True,
            show_basemap=False,
        ).get_root().render()

        self.assertIn("populationDensityPane.style.zIndex = 350", html)
        self.assertIn("earthquakePane.style.zIndex = 650", html)
        self.assertIn("earthquakePopupPane.style.zIndex = 750", html)
        self.assertIn('"pane": "populationDensityPane"', html)
        self.assertIn('map.getContainer().appendChild(this._canvas)', html)
        self.assertIn('map.on("move zoom resize", this._scheduleReset, this)', html)
        self.assertIn("origin.x - panePosition.x", html)

    def test_interpolation_builds_one_masked_surface_per_province(self) -> None:
        joined = join_population_density_geojson(
            self.subdistrict_geojson,
            self.density_df,
            require_complete=True,
        )
        surfaces = build_population_density_surfaces(joined)

        self.assertEqual(
            {surface["province"] for surface in surfaces},
            set(self.density_df["provinsi"].unique()),
        )
        self.assertTrue(all(surface["sample_count"] > 1 for surface in surfaces))
        self.assertTrue(
            all(
                surface["interpolation_method"] == "ordinary_kriging"
                for surface in surfaces
            )
        )
        self.assertTrue(all(surface["data_url"].startswith("data:image/png;base64,") for surface in surfaces))
        self.assertTrue(
            all(min(surface["image"].getchannel("A").getextrema()) == 0 for surface in surfaces)
        )


if __name__ == "__main__":
    unittest.main()
