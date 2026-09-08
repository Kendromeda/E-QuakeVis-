from __future__ import annotations

from datetime import date
import hashlib
from io import BytesIO
import math
from pathlib import Path
from typing import Any

import folium
import numpy as np
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from utils.data_loader import (
    COLUMN_LABELS,
    DATABASE_COLUMNS,
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
    DatabaseWriteError,
    DataValidationError,
    UnsupportedFileTypeError,
    apply_column_mapping,
    append_to_master_database,
    clean_earthquake_data,
    filter_earthquake_data,
    has_canonical_columns,
    inspect_uploaded_file,
    load_master_database,
    load_uploaded_file,
    normalize_for_database,
    suggest_column_mapping,
)
from utils.earthquake_store import (
    EarthquakeStoreError,
    default_query_dates,
    ensure_working_database,
    get_store_summary,
    query_earthquakes,
    working_database_signature,
)
from utils.fault_data import (
    FAULT_MAX_OFFSHORE_ASSOCIATION_KM,
    FaultDataError,
    build_fault_province_index,
    distance_to_fault_segments,
    enrich_earthquakes_with_faults,
    get_fault_names,
    get_fault_segment_options,
    load_fault_geojson,
)
from utils.map_builder import (
    BASEMAP_OPTIONS,
    BASEMAP_RELIEF,
    BASEMAP_SHORTBREAD,
    build_earthquake_feature_group,
    build_indonesia_map,
    filter_hidden_earthquakes,
    get_earthquake_event_id,
)
from utils.geology_catalog import (
    GEOLOGY_STYLE_OFFICIAL,
    GEOLOGY_STYLE_OPTIONS,
    GeologyUnit,
    merge_geology_units,
    query_geology_features_for_region,
    query_geology_units_for_province,
    query_geology_units_for_region,
)
from utils.population_density import (
    PopulationDensityDataError,
    filter_population_density,
    filter_population_density_geojson,
    join_population_density_geojson,
    load_population_density_csv,
    load_subdistrict_geojson,
)
from utils.seismic_station import (
    load_seismic_stations,
    seismic_station_signature,
)
from utils.publication_map import (
    PAGE_SIZES,
    PUBLICATION_DPI_OPTIONS,
    PUBLICATION_TEMPLATE_BANDUNG,
    PUBLICATION_TEMPLATE_LEGACY,
    PUBLICATION_TEMPLATE_RTRW,
    PUBLICATION_TEMPLATES,
    PublicationMapError,
    capture_filtered_map,
    get_publication_map_size,
    list_visible_geology_units,
    plan_geology_legend_overflow,
    publication_filename_stem,
    render_publication_map,
)
from utils.coastal_data import (
    CoastalDataError,
    coastal_region_names,
    distance_to_coast,
    load_coastal_regions,
)
from utils.region_data import (
    RegionDataError,
    enrich_earthquakes_with_regions,
    load_region_geojson,
)
from utils.slab_data import (
    SlabDataError,
    filter_slab_contours,
    load_slab_contours,
)
from utils.stats import calculate_summary_stats
from utils.trench_data import TrenchDataError, load_trench_geojson
from utils.volcano_data import (
    VolcanoDataError,
    distance_to_volcanoes,
    get_volcano_names,
    load_volcano_geojson,
)
APP_DISPLAY_NAME = "E-QuakeVis"
APP_DIR = Path(__file__).resolve().parent
SEED_DATA_PATH: Path | None = None
MASTER_DATA_PATH = APP_DIR / "data" / "gempa_master.csv"
CATALOG_ARCHIVE_DATA_PATH = (
    APP_DIR / "data" / "katalog_gempa_bmkg_2008_terbaru.csv"
)
WORKING_DATABASE_PATH = APP_DIR / "data" / "gempa_working.sqlite"
FAULT_DATA_PATH = APP_DIR / "data" / "sesar_pusgen_2024.geojson"
VOLCANO_DATA_PATH = APP_DIR / "data" / "krb_gunung_api_indonesia_big.geojson"
PROVINCE_DATA_PATH = APP_DIR / "data" / "provinsi_indonesia_38.geojson"
DISTRICT_DATA_PATH = APP_DIR / "data" / "kabupaten_kota_indonesia.geojson"
SUBDISTRICT_DATA_PATH = APP_DIR / "data" / "kecamatan_lightweight.geojson"
COASTAL_DATA_PATH = APP_DIR / "data" / "kabupaten_pesisir.json"
POPULATION_DENSITY_DATA_PATH = (
    APP_DIR / "data" / "kepadatan_penduduk_kecamatan.csv"
)
STATION_DATA_PATH = APP_DIR / "data" / "stasiun_seismik.csv"
# Ceiling for the "distance from coast" slider, kept small so the coastal filter
# stays a near-shore tool.
MAX_COAST_DISTANCE_KM = 50.0
DEFAULT_COAST_DISTANCE_KM = 50.0
TRENCH_DATA_PATH = APP_DIR / "data" / "indonesia_trench_slab2.geojson"
SLAB_DATA_PATH = APP_DIR / "data" / "sum_slab2_dep_contours.in"
SLAB_INTERVAL_OPTIONS = {"100 km": 100, "200 km": 200, "Semua (20 km)": 20}
ENRICHMENT_VERSION = "regions-land-sea-v3-province-mask"
TABLE_PREVIEW_LIMIT = 2_000
MAP_HEIGHT = 820
DEFAULT_QUERY_DAYS = 90

st.set_page_config(
    page_title=APP_DISPLAY_NAME,
    layout="wide",
)


def initialize_state() -> None:
    defaults = {
        "clean_df": None,
        "data_signature": None,
        "last_import_result": None,
        "active_import_signature": None,
        "publication_map_result": None,
        "hidden_earthquake_ids": [],
        "last_earthquake_map_action": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
            --brand: #0b5ea8;
            --brand-dark: #0a4f8c;
            --ink: #0f172a;
            --muted: #64748b;
            --line: #e2e8f0;
        }

        html, body,
        [data-testid="stAppViewContainer"],
        [data-testid="stSidebar"],
        [data-testid="stAppViewContainer"] p,
        [data-testid="stAppViewContainer"] label,
        [data-testid="stAppViewContainer"] button,
        [data-testid="stAppViewContainer"] input,
        [data-testid="stAppViewContainer"] textarea,
        [data-testid="stMetricValue"],
        .site-title,
        .section-heading {
            font-family: "Segoe UI", Roboto, "Helvetica Neue", Arial, "Liberation Sans", sans-serif;
        }

        /* Streamlit sets its own heading font; force ours so titles match too. */
        [data-testid="stAppViewContainer"] h1,
        [data-testid="stAppViewContainer"] h2,
        [data-testid="stAppViewContainer"] h3,
        [data-testid="stAppViewContainer"] h4,
        [data-testid="stAppViewContainer"] h5,
        [data-testid="stAppViewContainer"] h6 {
            font-family: "Segoe UI", Roboto, "Helvetica Neue", Arial, "Liberation Sans", sans-serif !important;
        }

        .stApp {
            background: #eef2f7;
        }

        .block-container {
            /* Sisakan ruang untuk toolbar Streamlit yang melayang di atas. */
            padding-top: 2.6rem;
            padding-bottom: 1.2rem;
            padding-left: 1.4rem;
            padding-right: 1.4rem;
            max-width: none;
        }

        /* Tab bar berperan sebagai navigasi utama, jadi dibuat lebih tegas. */
        [data-testid="stTabs"] [data-baseweb="tab-list"] {
            gap: 0.1rem;
            border-bottom: 1px solid var(--line);
            background: #ffffff;
            border-radius: 12px 12px 0 0;
            padding: 0 0.35rem;
            margin-bottom: 0.85rem;
        }

        [data-testid="stTabs"] [data-baseweb="tab"] {
            padding: 0.6rem 1.05rem;
            font-size: 0.9rem;
            color: var(--muted);
        }

        [data-testid="stTabs"] [data-baseweb="tab"]:hover {
            color: var(--ink);
        }

        [data-testid="stTabs"] [aria-selected="true"] {
            color: var(--brand);
            font-weight: 600;
        }

        [data-testid="stTabs"] [data-baseweb="tab-highlight"] {
            background: var(--brand);
            height: 2px;
        }

        [data-testid="stTabs"] [data-baseweb="tab-border"] {
            display: none;
        }

        /* Kartu bento: peta, blok statistik, dan tabel. */
        [data-testid="stVerticalBlockBorderWrapper"]:has(> div > [data-testid="stVerticalBlock"]) {
            background: #ffffff;
            border-radius: 14px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06),
                0 8px 24px rgba(15, 23, 42, 0.05);
        }

        [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] {
            box-shadow: none;
        }

        /* Judul bagian di dalam kartu tidak perlu garis tepi sendiri. */
        [data-testid="stVerticalBlockBorderWrapper"] .section-heading {
            margin-top: 0.15rem;
            font-size: 0.95rem;
        }

        /* Sidebar: label bagian dibuat kecil dan kapital seperti rancangan. */
        [data-testid="stSidebar"] h2,
        [data-testid="stSidebar"] h3 {
            font-size: 0.74rem !important;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: var(--muted) !important;
            margin: 1.1rem 0 0.35rem;
        }

        [data-testid="stSidebar"] [data-testid="stExpander"] details {
            border-radius: 10px;
            border-color: var(--line);
        }

        /* Peta memakai lebar penuh kartunya. */
        iframe[title="streamlit_folium.st_folium"] {
            width: 100% !important;
            border-radius: 12px;
        }

        [data-testid="stSidebar"] {
            background: #ffffff;
            border-right: 1px solid var(--line);
        }

        [data-testid="stMetric"] {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 13px;
            padding: 0.7rem 0.95rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06),
                0 8px 24px rgba(15, 23, 42, 0.05);
        }

        [data-testid="stMetricLabel"] p {
            color: var(--muted);
            font-size: 0.74rem;
            text-transform: none;
            letter-spacing: 0.01em;
        }

        [data-testid="stMetricValue"] {
            color: var(--brand-dark);
            font-size: 1.65rem;
            font-weight: 700;
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid var(--line);
            border-radius: 10px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
        }

        .stDownloadButton button,
        .stButton button {
            border-radius: 8px;
        }

        [data-testid="stBaseButton-primary"] {
            background: var(--brand) !important;
            border-color: var(--brand) !important;
        }

        [data-testid="stBaseButton-primary"]:hover {
            background: var(--brand-dark) !important;
            border-color: var(--brand-dark) !important;
        }

        /* Top bar ramping: identitas di kiri, status data di kanan. */
        .site-header {
            background: #ffffff;
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 0.6rem 0.95rem;
            margin-bottom: 0.55rem;
            display: flex;
            align-items: center;
            gap: 0.7rem;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
        }

        .site-mark {
            width: 30px;
            height: 30px;
            flex: none;
            border-radius: 9px;
            background: var(--brand);
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .site-header .site-title,
        .site-title {
            color: var(--ink) !important;
            font-size: 1.12rem;
            font-weight: 700;
            line-height: 1.2;
            margin: 0;
            letter-spacing: -0.015em;
        }

        .site-source {
            margin-left: auto;
            display: flex;
            align-items: center;
            gap: 0.45rem;
            font-size: 0.78rem;
            color: var(--muted);
            background: #f1f5f9;
            border-radius: 8px;
            padding: 0.32rem 0.7rem;
        }

        .site-source i {
            width: 7px;
            height: 7px;
            border-radius: 50%;
            background: #15803d;
            display: inline-block;
        }

        .section-heading {
            color: var(--ink);
            font-size: 1.1rem;
            font-weight: 700;
            margin: 1.2rem 0 0.6rem;
            padding-left: 0.6rem;
            border-left: 4px solid var(--brand);
        }

        .app-footer {
            color: var(--muted);
            font-size: 0.85rem;
            text-align: center;
            border-top: 1px solid var(--line);
            margin-top: 2.25rem;
            padding-top: 0.9rem;
        }

        </style>
        """,
        unsafe_allow_html=True,
    )


def get_master_database_signature() -> str:
    if not MASTER_DATA_PATH.exists():
        return "database:missing"

    master_stat = MASTER_DATA_PATH.stat()
    fault_signature = "faults:missing"
    if FAULT_DATA_PATH.exists():
        fault_stat = FAULT_DATA_PATH.stat()
        fault_signature = f"faults:{fault_stat.st_mtime_ns}:{fault_stat.st_size}"
    region_signatures: list[str] = []
    for path in (PROVINCE_DATA_PATH, DISTRICT_DATA_PATH):
        if path.exists():
            stat = path.stat()
            region_signatures.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        else:
            region_signatures.append(f"{path.name}:missing")
    return (
        f"database:{master_stat.st_mtime_ns}:{master_stat.st_size}:"
        f"{fault_signature}:{':'.join(region_signatures)}:{ENRICHMENT_VERSION}"
    )


def get_master_database_file_signature() -> tuple[int, int]:
    """Return a cheap cache key that changes whenever the master CSV changes."""
    if not MASTER_DATA_PATH.exists():
        return (0, 0)
    master_stat = MASTER_DATA_PATH.stat()
    return (master_stat.st_mtime_ns, master_stat.st_size)


@st.cache_data(show_spinner=False)
def load_database_file(modified_time_ns: int, file_size: int) -> pd.DataFrame:
    # The values are cache invalidators; the loader reads the configured path.
    del modified_time_ns, file_size
    return load_master_database(MASTER_DATA_PATH, SEED_DATA_PATH)


@st.cache_data(show_spinner=False)
def load_working_store_summary(
    modified_time_ns: int,
    file_size: int,
) -> dict[str, object]:
    del modified_time_ns, file_size
    return get_store_summary(WORKING_DATABASE_PATH)


@st.cache_data(show_spinner=False)
def load_working_store_query(
    modified_time_ns: int,
    file_size: int,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    del modified_time_ns, file_size
    return query_earthquakes(WORKING_DATABASE_PATH, start_date, end_date)


def get_archive_data_paths() -> tuple[Path, ...]:
    """Use the canonical master archive, with the downloaded catalog as seed fallback."""
    if MASTER_DATA_PATH.exists():
        return (MASTER_DATA_PATH,)
    if CATALOG_ARCHIVE_DATA_PATH.exists():
        return (CATALOG_ARCHIVE_DATA_PATH,)
    return ()


@st.cache_data(show_spinner=False)
def load_station_reference(path: str, modified_time_ns: int) -> list:
    del modified_time_ns
    return load_seismic_stations(path)


@st.cache_data(show_spinner=False)
def load_population_density_map_reference(
    density_path: str,
    density_modified_time_ns: int,
    boundary_path: str,
    boundary_modified_time_ns: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Load and join the complete density layer once per source snapshot."""
    del density_modified_time_ns, boundary_modified_time_ns
    density_df = load_population_density_csv(density_path)
    boundary_geojson = load_subdistrict_geojson(boundary_path)
    return density_df, join_population_density_geojson(
        boundary_geojson,
        density_df,
        require_complete=True,
    )


def _named_upload(content: bytes, filename: str) -> BytesIO:
    upload = BytesIO(content)
    upload.name = filename
    return upload


@st.cache_data(show_spinner=False)
def inspect_import_content(content: bytes, filename: str) -> dict[str, Any]:
    return inspect_uploaded_file(_named_upload(content, filename))


@st.cache_data(show_spinner=False)
def load_import_content(
    content: bytes,
    filename: str,
    sheet_name: str | None,
    header_row: int | None,
) -> pd.DataFrame:
    return load_uploaded_file(
        _named_upload(content, filename),
        sheet_name=sheet_name,
        header_row=header_row,
    )


@st.cache_data(show_spinner=False)
def load_fault_reference(path: str, modified_time: int) -> dict[str, Any]:
    del modified_time
    return load_fault_geojson(Path(path))


@st.cache_data(show_spinner=False)
def load_volcano_reference(path: str, modified_time: int) -> dict[str, Any]:
    del modified_time
    return load_volcano_geojson(Path(path))


@st.cache_data(show_spinner=False)
def load_region_reference(path: str, modified_time: int) -> dict[str, Any]:
    del modified_time
    return load_region_geojson(Path(path))


@st.cache_data(ttl=86_400, show_spinner=False)
def load_geology_units_for_province(
    province_name: str,
    province_boundary_signature: int,
    province_geojson: dict[str, Any],
) -> tuple[GeologyUnit, ...]:
    """Cache Layer 8 attributes; full polygon geometry remains on BIG's server."""
    del province_boundary_signature
    return query_geology_units_for_province(province_geojson, province_name)


@st.cache_data(ttl=86_400, show_spinner=False)
def load_geology_units_for_districts(
    district_names: tuple[str, ...],
    district_boundary_signature: int,
    district_geojson: dict[str, Any],
) -> tuple[GeologyUnit, ...]:
    """Return units intersecting the effective district filter for publication."""
    del district_boundary_signature
    return query_geology_units_for_region(
        district_geojson,
        district_names,
        property_name="WADMKK",
        timeout=15.0,
    )


@st.cache_data(show_spinner=False)
def load_coastal_reference(path: str, modified_time: int) -> dict[str, Any]:
    del modified_time
    return load_coastal_regions(Path(path))


@st.cache_data(show_spinner="Menghitung jarak gempa ke pantai...")
def compute_selected_coast_distances(
    data_signature: str,
    coastal_district_names: tuple[str, ...],
    _clean_df: pd.DataFrame,
    _coastal_regions: dict[str, Any],
) -> np.ndarray:
    del data_signature
    return distance_to_coast(_clean_df, _coastal_regions, coastal_district_names)


@st.cache_data(show_spinner=False)
def load_trench_reference(path: str, modified_time: int) -> dict[str, Any]:
    del modified_time
    return load_trench_geojson(Path(path))


@st.cache_data(show_spinner=False)
def load_slab_reference(path: str, modified_time: int) -> dict[str, Any]:
    del modified_time
    return load_slab_contours(Path(path))


@st.cache_data(show_spinner="Mengindeks sesar per provinsi...")
def load_fault_province_reference(
    fault_path: str,
    fault_modified_time: int,
    province_path: str,
    province_modified_time: int,
) -> dict[str, list[int]]:
    del fault_modified_time, province_modified_time
    return build_fault_province_index(
        load_fault_geojson(Path(fault_path)),
        load_region_geojson(Path(province_path)),
    )


@st.cache_data(show_spinner="Menghitung jarak ke segmen sesar terpilih...")
def compute_selected_fault_segment_distances(
    data_signature: str,
    selected_segment_indexes: tuple[int, ...],
    _clean_df: pd.DataFrame,
    _fault_geojson: dict[str, Any],
) -> np.ndarray:
    del data_signature
    return distance_to_fault_segments(
        _clean_df,
        _fault_geojson,
        selected_segment_indexes,
    )


@st.cache_data(show_spinner="Menghitung jarak ke gunung api terpilih...")
def compute_selected_volcano_distances(
    data_signature: str,
    selected_volcano_names: tuple[str, ...],
    _clean_df: pd.DataFrame,
    _volcano_geojson: dict[str, Any],
) -> np.ndarray:
    del data_signature
    return distance_to_volcanoes(
        _clean_df,
        _volcano_geojson,
        selected_volcano_names,
    )


@st.cache_data(show_spinner="Menghubungkan gempa dengan sesar terdekat...")
def enrich_with_fault_reference(
    df: pd.DataFrame,
    fault_geojson: dict[str, Any],
) -> pd.DataFrame:
    return enrich_earthquakes_with_faults(df, fault_geojson)


@st.cache_data(show_spinner="Menentukan wilayah administrasi...")
def enrich_with_region_reference(
    df: pd.DataFrame,
    province_geojson: dict[str, Any],
    district_geojson: dict[str, Any],
) -> pd.DataFrame:
    return enrich_earthquakes_with_regions(
        df,
        province_geojson,
        district_geojson,
    )


def format_metric(value: float | int | None, suffix: str = "") -> str:
    if value is None:
        return "-"
    if isinstance(value, int):
        return f"{value:,}".replace(",", ".")
    return f"{value:.2f}{suffix}"


def render_catalog_date_query(
    summary: dict[str, object],
    database_signature: tuple[int, int],
) -> tuple[date, date] | None:
    """Render the indexed SQLite date query, defaulting to the latest 90 days."""
    minimum_date = pd.Timestamp(summary["minimum_time"]).date()
    maximum_date = pd.Timestamp(summary["maximum_time"]).date()
    default_dates = default_query_dates(summary, DEFAULT_QUERY_DAYS)
    state_signature = (
        database_signature,
        minimum_date.isoformat(),
        maximum_date.isoformat(),
    )
    if st.session_state.get("_catalog_date_state_signature") != state_signature:
        st.session_state["_catalog_date_state_signature"] = state_signature
        st.session_state["catalog_query_date_range"] = default_dates

    st.sidebar.header("Filter")
    selected = st.sidebar.date_input(
        "Tanggal",
        min_value=minimum_date,
        max_value=maximum_date,
        key="catalog_query_date_range",
        help=(
            "Query awal hanya memuat 90 hari terbaru dari SQLite. Rentang lama "
            "tetap dapat dipilih tanpa mengubah arsip CSV."
        ),
    )
    if not isinstance(selected, tuple) or len(selected) != 2:
        st.sidebar.warning("Pilih tanggal awal dan akhir.")
        return None
    start_date, end_date = selected
    if start_date > end_date:
        st.sidebar.warning("Tanggal awal melebihi tanggal akhir.")
        return None
    st.session_state["publication_date_range"] = (start_date, end_date)
    return start_date, end_date


def get_map_component_key(
    prefix: str,
    df: pd.DataFrame,
    *settings: object,
) -> str:
    """Key the stable map shell independently from its replaceable point layer."""
    signature_columns = [
        column
        for column in (
            "waktu",
            "lintang",
            "bujur",
            "kedalaman",
            "magnitudo",
            "jarak_sesar_km",
        )
        if column in df.columns
    ]
    digest_builder = hashlib.sha256()
    digest_builder.update(b"map-shell-dynamic-canvas-points-v2")
    if signature_columns:
        row_hashes = pd.util.hash_pandas_object(
            df[signature_columns],
            index=False,
        ).to_numpy()
        digest_builder.update(row_hashes.tobytes())
    digest_builder.update("|".join(str(setting) for setting in settings).encode("utf-8"))
    digest = digest_builder.hexdigest()[:20]
    return f"{prefix}-{digest}"


def find_earthquake_by_event_id(
    df: pd.DataFrame,
    event_id: str | None,
) -> Any | None:
    """Locate an earthquake row by the stable identifier used by the map."""
    if not event_id:
        return None
    for row in df.itertuples(index=False):
        if get_earthquake_event_id(row) == event_id:
            return row
    return None


def clear_hidden_earthquakes() -> None:
    """Restore all manually hidden events."""
    st.session_state.hidden_earthquake_ids = []


BRAND_MARK = (
    '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#ffffff" '
    'stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>'
)


def render_page_header(source_label: str | None = None) -> None:
    source_html = (
        f'<span class="site-source"><i></i>{source_label}</span>' if source_label else ""
    )
    st.markdown(
        f"""
        <div class="site-header">
            <span class="site-mark">{BRAND_MARK}</span>
            <h1 class="site-title">E-QuakeVis</h1>
            {source_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def safe_slider_bounds(low: float, high: float, pad: float) -> tuple[float, float]:
    """Guarantee an adjustable slider range even when data has a single value.

    Streamlit freezes a slider whose ``min_value`` equals ``max_value``; when the
    filtered data collapses to one depth/magnitude/distance we keep the real
    minimum and add headroom above so both handles stay draggable.
    """
    low = float(low)
    high = float(high)
    if not (math.isfinite(low) and math.isfinite(high)):
        return 0.0, pad
    if high <= low:
        return low, low + pad
    return low, high


def get_district_filter_options(
    district_geojson: dict[str, Any],
    province_names: list[str] | None = None,
) -> list[str]:
    """Return complete administrative choices, including cities with no events."""
    selected_provinces = set(province_names or [])
    return sorted(
        {
            str(name)
            for feature in district_geojson.get("features", [])
            if (
                not selected_provinces
                or str((feature.get("properties") or {}).get("WADMPR") or "")
                in selected_provinces
            )
            if (name := (feature.get("properties") or {}).get("WADMKK"))
        }
    )


def render_sidebar_filters(
    clean_df: pd.DataFrame,
    population_density_df: pd.DataFrame,
    province_geojson: dict[str, Any],
    district_geojson: dict[str, Any],
    fault_province_index: dict[str, list[int]],
    all_fault_names: list[str],
    fault_names_by_index: dict[int, str],
    all_province_names: list[str],
    fault_geojson: dict[str, Any],
    all_volcano_names: list[str],
    volcano_geojson: dict[str, Any],
    coastal_regions: dict[str, Any],
    data_signature: str,
    date_value: tuple[date, date],
) -> tuple[Any, ...]:
    min_depth, max_depth = safe_slider_bounds(
        clean_df["kedalaman"].min(),
        clean_df["kedalaman"].max(),
        1.0,
    )
    min_magnitude, max_magnitude = safe_slider_bounds(
        clean_df["magnitudo"].min(),
        clean_df["magnitudo"].max(),
        0.1,
    )
    depth_value = st.sidebar.slider(
        "Kedalaman (km)",
        min_value=min_depth,
        max_value=max_depth,
        value=(min_depth, max_depth),
        help="Satuan km",
    )
    magnitude_value = st.sidebar.slider(
        "Magnitudo (M)",
        min_value=min_magnitude,
        max_value=max_magnitude,
        value=(min_magnitude, max_magnitude),
        step=0.1,
    )
    # Expose the active period so the publication title can follow it.
    if (
        isinstance(date_value, tuple)
        and len(date_value) == 2
        and date_value[0] <= date_value[1]
    ):
        st.session_state["publication_date_range"] = date_value
    else:
        st.session_state["publication_date_range"] = None

    selected_provinces = st.sidebar.multiselect(
        "Provinsi",
        options=all_province_names,
        placeholder="Semua provinsi",
    )

    district_options = get_district_filter_options(
        district_geojson,
        selected_provinces,
    )
    selected_districts = st.sidebar.multiselect(
        "Kabupaten/Kota",
        options=district_options,
        placeholder="Semua kabupaten/kota",
    )

    # A distance-from-coast filter only makes sense when a selected kabupaten/kota
    # actually faces the sea, so the slider appears only for coastal selections.
    coast_distance_series: pd.Series | None = None
    max_selected_coast_distance: float | None = None
    coastal_selected = coastal_region_names(coastal_regions, selected_districts)
    if coastal_selected:
        coast_distances = compute_selected_coast_distances(
            data_signature,
            tuple(coastal_selected),
            clean_df,
            coastal_regions,
        )
        coast_distance_series = pd.Series(coast_distances, index=clean_df.index)
        max_selected_coast_distance = st.sidebar.slider(
            "Jarak maksimum dari pantai (km)",
            min_value=0.0,
            max_value=MAX_COAST_DISTANCE_KM,
            value=min(DEFAULT_COAST_DISTANCE_KM, MAX_COAST_DISTANCE_KM),
            step=5.0,
            help=(
                "Muncul karena kabupaten/kota terpilih berpantai. Menyaring gempa "
                "berdasarkan jarak permukaan 2D ke garis pantai daerah tersebut, "
                "termasuk gempa lepas pantai, digabung dengan gempa di daratannya. "
                "Kedekatan ke pantai bukan penentu penyebab gempa."
            ),
        )
        finite_coast = coast_distances[np.isfinite(coast_distances)]
        within_coast = int((finite_coast <= max_selected_coast_distance).sum())
        st.sidebar.caption(
            f"{within_coast} gempa dalam ≤ {max_selected_coast_distance:.0f} km "
            f"dari pantai {', '.join(coastal_selected)}."
        )

    selected_location_types = st.sidebar.multiselect(
        "Lokasi episentrum",
        options=["Darat", "Laut"],
        default=["Darat", "Laut"],
        help="Titik laut dipasangkan ke provinsi daratan terdekat hingga 400 km.",
    )

    fault_distance_series: pd.Series | None = None
    max_selected_fault_distance: float | None = None
    volcano_distance_series: pd.Series | None = None
    max_selected_volcano_distance: float | None = None

    st.sidebar.subheader("Tampilan Peta")
    basemap_name = st.sidebar.selectbox(
        "Jenis basemap",
        options=BASEMAP_OPTIONS,
        index=BASEMAP_OPTIONS.index(BASEMAP_RELIEF),
        help="Semua basemap dapat digunakan tanpa API key.",
    )
    show_basemap = st.sidebar.toggle(
        "Tampilkan basemap",
        value=True,
        help="Aktifkan atau sembunyikan layer dasar yang dipilih.",
    )
    show_shortbread_roads = False
    show_shortbread_rivers = True
    show_shortbread_topography = False
    if basemap_name == BASEMAP_SHORTBREAD:
        show_shortbread_topography = st.sidebar.toggle(
            "Tampilkan topografi",
            value=False,
            key="show_shortbread_topography_fast",
            help="Relief raster Esri World Hillshade tanpa API key aplikasi.",
        )
        show_shortbread_roads = st.sidebar.toggle(
            "Tampilkan jalan",
            value=False,
            help="Hanya berlaku untuk basemap Shortbread.",
        )
        show_shortbread_rivers = st.sidebar.toggle(
            "Tampilkan sungai",
            value=True,
            key="show_shortbread_rivers",
            help="Sungai dan aliran alami pada basemap Shortbread.",
        )
    show_earthquakes = st.sidebar.toggle("Tampilkan titik gempa", value=True)
    show_population_density = st.sidebar.toggle(
        "Overlay kepadatan penduduk",
        value=False,
        help=(
            "Filter provinsi memakai ordinary kriging tanpa batas kecamatan. Filter "
            "kabupaten/kota menampilkan kembali poligon dan batas kecamatan."
        ),
    )
    selected_density_provinces = list(selected_provinces)
    selected_density_regions = list(selected_districts)
    density_scope = filter_population_density(
        population_density_df,
        selected_density_regions,
        None,
        selected_density_provinces,
    )
    range_source = density_scope if not density_scope.empty else population_density_df
    density_min = math.floor(range_source["kepadatan_penduduk_per_km2"].min())
    density_max = math.ceil(range_source["kepadatan_penduduk_per_km2"].max())
    population_density_range = (density_min, density_max)
    if show_population_density:
        visible_density_count = len(density_scope)
        if selected_districts:
            density_description = (
                f"{visible_density_count} kecamatan ditampilkan sebagai poligon "
                "dengan batas kecamatan dan 7 kelas warna lokal."
            )
        else:
            density_description = (
                f"{visible_density_count} titik kecamatan diolah dengan ordinary "
                "kriging per provinsi "
                "dengan 9 kelas warna adaptif."
            )
        st.sidebar.caption(
            f"{density_description} Rentang otomatis {density_min:,}–{density_max:,} "
            "jiwa/km².".replace(",", ".")
        )
    show_geology = st.sidebar.toggle(
        "Layer geologi — BIG/KSP",
        value=False,
        help=(
            "Poligon resmi Peta Geologi Layer 8 PSG/BIG. Warna menunjukkan "
            "umur batuan, sedangkan nama/sandi menunjukkan unit atau formasi."
        ),
    )
    geology_style_mode = GEOLOGY_STYLE_OFFICIAL
    if show_geology:
        geology_style_mode = st.sidebar.selectbox(
            "Mode warna geologi",
            options=GEOLOGY_STYLE_OPTIONS,
            index=0,
            help=(
                "Resmi BIG/KSP mengikuti renderer Layer 8 apa adanya. CGMW 2022 "
                "adalah alternatif standardisasi warna umur internasional."
            ),
            key="geology_style_mode",
        )
    geology_units: tuple[GeologyUnit, ...] = ()
    geology_index_units: tuple[GeologyUnit, ...] | None = None
    geology_catalog_loaded = False
    selected_geology_keys: list[str] = []
    if show_geology and not selected_provinces:
        st.sidebar.caption(
            "Pilih minimal satu provinsi untuk membuka filter unit/formasi. "
            "Layer nasional tetap dapat ditampilkan tanpa filter formasi."
        )
    elif show_geology:
        try:
            with st.sidebar:
                with st.spinner("Memuat indeks unit/formasi geologi…"):
                    geology_units = merge_geology_units(
                        load_geology_units_for_province(
                            province_name,
                            PROVINCE_DATA_PATH.stat().st_mtime_ns,
                            province_geojson,
                        )
                        for province_name in selected_provinces
                    )
                    geology_catalog_loaded = True
        except (OSError, ValueError, TimeoutError) as exc:
            st.sidebar.warning(
                "Indeks formasi BIG sedang tidak dapat dimuat. Layer geologi "
                f"tetap tersedia tanpa filter formasi. ({exc})"
            )
        if geology_catalog_loaded:
            geology_index_units = geology_units
            if selected_districts:
                try:
                    geology_index_units = load_geology_units_for_districts(
                        tuple(sorted(selected_districts)),
                        DISTRICT_DATA_PATH.stat().st_mtime_ns,
                        district_geojson,
                    )
                except (OSError, ValueError, TimeoutError) as exc:
                    geology_index_units = None
                    st.sidebar.warning(
                        "Indeks publikasi untuk kabupaten/kota belum dapat "
                        f"diverifikasi dari BIG. ({exc})"
                    )
        if geology_units:
            unit_by_key = {unit.key: unit for unit in geology_units}
            stored_geology_keys = list(
                st.session_state.get("selected_geology_formation_keys", [])
            )
            valid_stored_keys = [
                key for key in stored_geology_keys if key in unit_by_key
            ]
            if valid_stored_keys != stored_geology_keys:
                st.session_state["selected_geology_formation_keys"] = (
                    valid_stored_keys
                )
            selected_geology_keys = st.sidebar.multiselect(
                "Unit/Formasi geologi",
                options=list(unit_by_key),
                format_func=lambda key: (
                    f"{unit_by_key[key].display_name} ({unit_by_key[key].age})"
                ),
                placeholder="Semua unit yang beririsan",
                help=(
                    "Daftar atribut tanpa geometri, dicache 24 jam. Kosong berarti "
                    "semua unit yang beririsan dengan provinsi terpilih."
                ),
                key="selected_geology_formation_keys",
            )
            st.sidebar.caption(
                f"{len(geology_units)} unit/formasi beririsan dengan provinsi "
                "terpilih; geometri peta tetap dirender sebagai PNG ringan."
            )
            if selected_districts and geology_index_units is not None:
                st.sidebar.caption(
                    f"Indeks publikasi dibatasi menjadi {len(geology_index_units)} "
                    "unit yang beririsan dengan kabupaten/kota aktif."
                )
            if selected_geology_keys:
                with st.sidebar.expander("Keterangan unit terpilih"):
                    for key in selected_geology_keys:
                        unit = unit_by_key[key]
                        st.markdown(f"**{unit.display_name}**")
                        st.caption(unit.description)
    show_faults = st.sidebar.toggle("Tampilkan sesar", value=True)
    selected_fault_provinces: list[str] = []
    if show_faults:
        selected_fault_provinces = st.sidebar.multiselect(
            "Provinsi sesar",
            options=all_province_names,
            placeholder="Semua wilayah sesar",
            help=(
                "Mencakup sesar darat dan sesar lepas pantai terdekat hingga "
                f"{FAULT_MAX_OFFSHORE_ASSOCIATION_KM:.0f} km."
            ),
        )
    province_fault_indexes = sorted(
        {
            feature_index
            for province_name in selected_fault_provinces
            for feature_index in fault_province_index.get(province_name, [])
        }
    )
    map_fault_options = sorted(
        {
            fault_names_by_index[feature_index]
            for feature_index in province_fault_indexes
            if feature_index in fault_names_by_index
        }
    ) if selected_fault_provinces else all_fault_names
    selected_map_faults: list[str] = []
    selected_fault_segment_indexes: list[int] = []
    active_fault_segment_indexes: list[int] = []
    if show_faults:
        selected_map_faults = st.sidebar.multiselect(
            "Sesar pada peta",
            options=map_fault_options,
            placeholder="Semua sesar di wilayah",
            help="Pilihan provinsi juga mencakup sesar lepas pantai terdekat.",
        )
        if selected_map_faults:
            fault_segment_options = get_fault_segment_options(
                fault_geojson,
                selected_map_faults,
            )
            segment_option_indexes = list(fault_segment_options)
            segment_options_signature = tuple(sorted(selected_map_faults))
            if (
                st.session_state.get("_fault_segment_options_signature")
                != segment_options_signature
            ):
                st.session_state["_fault_segment_options_signature"] = (
                    segment_options_signature
                )
                st.session_state["selected_fault_segment_indexes"] = []
            selected_fault_segment_indexes = st.sidebar.multiselect(
                "Segmen sesar",
                options=segment_option_indexes,
                default=[],
                format_func=lambda feature_index: fault_segment_options[
                    feature_index
                ],
                key="selected_fault_segment_indexes",
                placeholder="Semua segmen",
                help=(
                    "Biarkan kosong untuk menampilkan semua segmen dari sesar "
                    "terpilih. Pilih satu atau beberapa nama untuk membatasi segmen."
                ),
            )
            active_fault_segment_indexes = (
                selected_fault_segment_indexes or segment_option_indexes
            )
            if not selected_fault_segment_indexes:
                st.sidebar.caption(
                    f"Semua {len(segment_option_indexes)} segmen ditampilkan."
                )
        if active_fault_segment_indexes:
            distances = compute_selected_fault_segment_distances(
                data_signature,
                tuple(sorted(active_fault_segment_indexes)),
                clean_df,
                fault_geojson,
            )
            fault_distance_series = pd.Series(distances, index=clean_df.index)
            finite_distances = distances[np.isfinite(distances)]
            distance_ceiling = (
                float(math.ceil(finite_distances.max()))
                if finite_distances.size
                else 1.0
            )
            distance_ceiling = max(2.0, distance_ceiling)
            selection_signature = tuple(sorted(active_fault_segment_indexes))
            if st.session_state.get("_fault_distance_signature") != selection_signature:
                st.session_state["_fault_distance_signature"] = selection_signature
                st.session_state["fault_distance_value"] = distance_ceiling
            st.session_state["fault_distance_value"] = float(
                min(max(1.0, st.session_state["fault_distance_value"]), distance_ceiling)
            )
            st.session_state["fault_distance_slider"] = st.session_state["fault_distance_value"]
            st.session_state["fault_distance_number"] = st.session_state["fault_distance_value"]

            def _sync_fault_distance_slider() -> None:
                st.session_state["fault_distance_value"] = st.session_state[
                    "fault_distance_slider"
                ]

            def _sync_fault_distance_number() -> None:
                st.session_state["fault_distance_value"] = st.session_state[
                    "fault_distance_number"
                ]

            st.sidebar.slider(
                "Jarak maksimum ke segmen sesar (km)",
                min_value=1.0,
                max_value=distance_ceiling,
                step=1.0,
                key="fault_distance_slider",
                on_change=_sync_fault_distance_slider,
                help=(
                    "Default menampilkan semua gempa. Geser atau ketik angka untuk "
                    "menyaring berdasarkan jarak permukaan 2D ke segmen sesar terpilih. "
                    "Filter ini tidak menentukan sesar penyebab gempa."
                ),
            )
            st.sidebar.number_input(
                "Ketik jarak (km)",
                min_value=1.0,
                max_value=distance_ceiling,
                step=1.0,
                key="fault_distance_number",
                on_change=_sync_fault_distance_number,
            )
            max_selected_fault_distance = float(st.session_state["fault_distance_value"])
            within_count = int(
                (finite_distances <= max_selected_fault_distance).sum()
            )
            st.sidebar.caption(
                f"{within_count} gempa dalam ≤ {max_selected_fault_distance:.0f} km "
                "secara horizontal dari segmen sesar terpilih."
            )
    visible_fault_indexes: list[int] | None
    map_scope_provinces = list(selected_provinces)
    if selected_districts and not map_scope_provinces:
        map_scope_provinces = sorted(
            {
                str((feature.get("properties") or {}).get("WADMPR") or "")
                for feature in district_geojson.get("features", [])
                if (feature.get("properties") or {}).get("WADMKK")
                in set(selected_districts)
            }
            - {""}
        )
    if selected_map_faults:
        visible_fault_indexes = active_fault_segment_indexes
    elif selected_fault_provinces:
        visible_fault_indexes = province_fault_indexes
    elif map_scope_provinces:
        visible_fault_indexes = sorted(
            {
                feature_index
                for province_name in map_scope_provinces
                for feature_index in fault_province_index.get(province_name, [])
            }
        )
    else:
        visible_fault_indexes = None
    show_volcanoes = st.sidebar.toggle(
        "Tampilkan gunung api",
        value=False,
        help=(
            "Tampilkan batas resmi Kawasan Rawan Bencana (KRB) gunung api dari BIG."
        ),
    )
    selected_volcano_names: list[str] = []
    if show_volcanoes:
        pending_volcano = st.session_state.pop("_pending_volcano_selection", None)
        if pending_volcano in all_volcano_names:
            st.session_state["selected_volcano_names"] = [pending_volcano]
        selected_volcano_names = st.sidebar.multiselect(
            "Gunung api pada peta",
            options=all_volcano_names,
            key="selected_volcano_names",
            placeholder="Semua gunung api",
            help=(
                "Biarkan kosong untuk menampilkan semua batas KRB. Pilih dari daftar "
                "atau klik area KRB gunung api pada peta."
            ),
        )
        if selected_volcano_names:
            distances = compute_selected_volcano_distances(
                data_signature,
                tuple(sorted(selected_volcano_names)),
                clean_df,
                volcano_geojson,
            )
            volcano_distance_series = pd.Series(distances, index=clean_df.index)
            finite_distances = distances[np.isfinite(distances)]
            distance_ceiling = (
                float(math.ceil(finite_distances.max()))
                if finite_distances.size
                else 1.0
            )
            distance_ceiling = max(2.0, distance_ceiling)
            selection_signature = tuple(sorted(selected_volcano_names))
            if (
                st.session_state.get("_volcano_distance_signature")
                != selection_signature
            ):
                st.session_state["_volcano_distance_signature"] = selection_signature
                st.session_state["volcano_distance_value"] = distance_ceiling
            st.session_state["volcano_distance_value"] = float(
                min(
                    max(1.0, st.session_state["volcano_distance_value"]),
                    distance_ceiling,
                )
            )
            st.session_state["volcano_distance_slider"] = st.session_state[
                "volcano_distance_value"
            ]
            st.session_state["volcano_distance_number"] = st.session_state[
                "volcano_distance_value"
            ]

            def _sync_volcano_distance_slider() -> None:
                st.session_state["volcano_distance_value"] = st.session_state[
                    "volcano_distance_slider"
                ]

            def _sync_volcano_distance_number() -> None:
                st.session_state["volcano_distance_value"] = st.session_state[
                    "volcano_distance_number"
                ]

            st.sidebar.slider(
                "Jarak maksimum ke gunung api (km)",
                min_value=1.0,
                max_value=distance_ceiling,
                step=1.0,
                key="volcano_distance_slider",
                on_change=_sync_volcano_distance_slider,
                help=(
                    "Jarak dihitung dari pusat KRB gunung api terpilih sebagai lintasan "
                    "permukaan terpendek dan tidak menyatakan hubungan penyebab."
                ),
            )
            st.sidebar.number_input(
                "Ketik jarak gunung api (km)",
                min_value=1.0,
                max_value=distance_ceiling,
                step=1.0,
                key="volcano_distance_number",
                on_change=_sync_volcano_distance_number,
            )
            max_selected_volcano_distance = float(
                st.session_state["volcano_distance_value"]
            )
            within_count = int(
                (finite_distances <= max_selected_volcano_distance).sum()
            )
            st.sidebar.caption(
                f"{within_count} gempa dalam ≤ {max_selected_volcano_distance:.0f} km "
                "dari gunung api terpilih."
            )
    show_trenches = st.sidebar.toggle("Tampilkan trench", value=True)
    st.sidebar.toggle(
        "Stasiun seismik",
        value=False,
        key="show_stations",
        help="Tampilkan lokasi stasiun seismik sebagai segitiga oranye.",
    )
    show_slabs = st.sidebar.toggle(
        "Tampilkan kontur slab",
        value=False,
        help="Kontur kedalaman zona subduksi (Slab2 USGS, region Sumatra-Java).",
    )
    slab_interval_km = 100
    if show_slabs:
        slab_interval_km = SLAB_INTERVAL_OPTIONS[
            st.sidebar.selectbox(
                "Interval kontur slab",
                options=list(SLAB_INTERVAL_OPTIONS),
                index=0,
                help="Jarak antar garis kontur kedalaman slab.",
            )
        ]
    show_provinces = st.sidebar.toggle("Tampilkan batas provinsi", value=False)
    show_region_labels = st.sidebar.toggle("Tampilkan nama daerah", value=True)

    if not isinstance(date_value, tuple) or len(date_value) != 2:
        st.sidebar.warning("Pilih rentang tanggal.")
        return (
            clean_df.iloc[0:0],
            selected_provinces,
            selected_districts,
            visible_fault_indexes,
            show_basemap,
            basemap_name,
            show_shortbread_roads,
            show_shortbread_rivers,
            show_shortbread_topography,
            show_earthquakes,
            show_faults,
            selected_volcano_names,
            show_volcanoes,
            show_trenches,
            show_provinces,
            show_region_labels,
            show_slabs,
            slab_interval_km,
            show_population_density,
            selected_density_provinces,
            selected_density_regions,
            population_density_range,
            show_geology,
            geology_style_mode,
            [unit.to_dict() for unit in geology_units],
            (
                None
                if geology_index_units is None
                else [unit.to_dict() for unit in geology_index_units]
            ),
            selected_geology_keys,
        )

    start_date, end_date = date_value
    if start_date > end_date:
        st.sidebar.warning("Tanggal awal melebihi tanggal akhir.")
        return (
            clean_df.iloc[0:0],
            selected_provinces,
            selected_districts,
            visible_fault_indexes,
            show_basemap,
            basemap_name,
            show_shortbread_roads,
            show_shortbread_rivers,
            show_shortbread_topography,
            show_earthquakes,
            show_faults,
            selected_volcano_names,
            show_volcanoes,
            show_trenches,
            show_provinces,
            show_region_labels,
            show_slabs,
            slab_interval_km,
            show_population_density,
            selected_density_provinces,
            selected_density_regions,
            population_density_range,
            show_geology,
            geology_style_mode,
            [unit.to_dict() for unit in geology_units],
            (
                None
                if geology_index_units is None
                else [unit.to_dict() for unit in geology_index_units]
            ),
            selected_geology_keys,
        )

    return (
        filter_earthquake_data(
            clean_df,
            depth_range=depth_value,
            magnitude_range=magnitude_value,
            date_range=(pd.Timestamp(start_date), pd.Timestamp(end_date)),
            province_names=selected_provinces,
            district_names=selected_districts,
            location_types=selected_location_types,
            fault_distances=fault_distance_series,
            max_fault_distance=max_selected_fault_distance,
            volcano_distances=volcano_distance_series,
            max_volcano_distance=max_selected_volcano_distance,
            coast_distances=coast_distance_series,
            max_coast_distance=max_selected_coast_distance,
        ),
        selected_provinces,
        selected_districts,
        visible_fault_indexes,
        show_basemap,
        basemap_name,
        show_shortbread_roads,
        show_shortbread_rivers,
        show_shortbread_topography,
        show_earthquakes,
        show_faults,
        selected_volcano_names,
        show_volcanoes,
        show_trenches,
        show_provinces,
        show_region_labels,
        show_slabs,
        slab_interval_km,
        show_population_density,
        selected_density_provinces,
        selected_density_regions,
        population_density_range,
        show_geology,
        geology_style_mode,
        [unit.to_dict() for unit in geology_units],
        (
            None
            if geology_index_units is None
            else [unit.to_dict() for unit in geology_index_units]
        ),
        selected_geology_keys,
    )


def render_import_panel() -> None:
    st.sidebar.header("Impor")
    uploaded_file = st.sidebar.file_uploader(
        "Tambah data",
        type=["csv", "xlsx", "xls"],
        accept_multiple_files=False,
        help="CSV atau Excel. Format yang cocok langsung siap ditambahkan.",
    )

    if uploaded_file is None:
        if st.session_state.last_import_result is not None:
            result = st.session_state.last_import_result
            merged_text = (
                f", {result.get('merged_rows', 0)} duplikat digabung"
                if result.get("merged_rows", 0)
                else ""
            )
            st.sidebar.success(
                f"Impor selesai: {result['added_rows']} baris baru{merged_text}, "
                f"total {result['total_rows']} baris."
            )
        return

    content = uploaded_file.getvalue()
    upload_signature = hashlib.sha256(content).hexdigest()[:16]
    if st.session_state.active_import_signature != upload_signature:
        st.session_state.active_import_signature = upload_signature
        st.session_state.last_import_result = None

    if st.session_state.last_import_result is not None:
        result = st.session_state.last_import_result
        merged_text = (
            f", {result.get('merged_rows', 0)} duplikat digabung"
            if result.get("merged_rows", 0)
            else ""
        )
        st.sidebar.success(
            f"Impor selesai: {result['added_rows']} baris baru{merged_text}, "
            f"total {result['total_rows']} baris."
        )

    try:
        inspection = inspect_import_content(content, uploaded_file.name)
    except UnsupportedFileTypeError as exc:
        st.sidebar.error(str(exc))
        return

    selected_sheet: str | None = None
    header_row: int | None = None
    sheet_detail: dict[str, Any] | None = None
    if inspection["is_excel"]:
        sheet_details = inspection["sheets"]
        likely_sheets = [
            detail for detail in sheet_details if detail["required_matches"] >= 4
        ] or sheet_details
        likely_names = [detail["name"] for detail in likely_sheets]
        suggested_sheet = inspection.get("suggested_sheet")
        suggested_index = (
            likely_names.index(suggested_sheet)
            if suggested_sheet in likely_names
            else 0
        )
        if len(likely_names) > 1:
            selected_sheet = st.sidebar.selectbox(
                "Sheet sumber",
                options=likely_names,
                index=suggested_index,
                key=f"import-sheet-{upload_signature}",
                help="Pilih sheet yang datanya ingin ditambahkan.",
            )
        elif likely_names:
            selected_sheet = likely_names[0]
            st.sidebar.caption(f"Sheet terdeteksi: **{selected_sheet}**")
        sheet_detail = next(
            (
                detail
                for detail in sheet_details
                if detail["name"] == selected_sheet
            ),
            None,
        )
        header_row = int(sheet_detail["header_row"]) if sheet_detail else 0
        if sheet_detail is not None and not sheet_detail["canonical"]:
            with st.sidebar.expander("Pengaturan sumber", expanded=False):
                st.caption(
                    "Baris judul terdeteksi otomatis. Ubah hanya jika nama kolom "
                    "di bawah belum sesuai."
                )
                header_row = int(
                    st.number_input(
                        "Baris judul kolom",
                        min_value=1,
                        max_value=50,
                        value=header_row + 1,
                        step=1,
                        key=f"import-header-{upload_signature}-{selected_sheet}",
                    )
                ) - 1

    try:
        raw_df = load_import_content(
            content,
            uploaded_file.name,
            selected_sheet,
            header_row,
        )
    except UnsupportedFileTypeError as exc:
        st.sidebar.error(str(exc))
        return

    import_df: pd.DataFrame | None = raw_df
    mapping_complete = True
    if has_canonical_columns(raw_df.columns):
        st.sidebar.caption("Format database dikenali otomatis")
    else:
        suggestions = suggest_column_mapping(raw_df.columns)
        source_options: list[str | None] = [None, *map(str, raw_df.columns)]
        mapping: dict[str, str | None] = {}
        with st.sidebar.expander("Cocokkan kolom", expanded=True):
            st.caption(
                "Saran sudah diisi otomatis. Periksa kolom bertanda * sebelum "
                "menambahkan data."
            )
            for target in REQUIRED_COLUMNS:
                suggestion = suggestions.get(target)
                mapping[target] = st.selectbox(
                    f"{COLUMN_LABELS[target]} *",
                    options=source_options,
                    index=(
                        source_options.index(suggestion)
                        if suggestion in source_options
                        else 0
                    ),
                    format_func=lambda value: "Pilih kolom" if value is None else value,
                    key=f"import-map-{upload_signature}-{selected_sheet}-{header_row}-{target}",
                )
            st.caption("Kolom tambahan (opsional)")
            for target in OPTIONAL_COLUMNS:
                suggestion = suggestions.get(target)
                mapping[target] = st.selectbox(
                    COLUMN_LABELS[target],
                    options=source_options,
                    index=(
                        source_options.index(suggestion)
                        if suggestion in source_options
                        else 0
                    ),
                    format_func=lambda value: "Tidak digunakan" if value is None else value,
                    key=f"import-map-{upload_signature}-{selected_sheet}-{header_row}-{target}",
                )

        mapping_complete = all(mapping.get(column) for column in REQUIRED_COLUMNS)
        duplicate_sources = [source for source in mapping.values() if source]
        if len(duplicate_sources) != len(set(duplicate_sources)):
            mapping_complete = False
            st.sidebar.warning("Setiap tujuan harus memakai kolom sumber yang berbeda.")
        if mapping_complete:
            try:
                import_df = apply_column_mapping(raw_df, mapping)
            except DataValidationError as exc:
                import_df = None
                st.sidebar.error(str(exc))
        else:
            import_df = None

    valid_preview: pd.DataFrame | None = None
    if import_df is not None:
        try:
            valid_preview = normalize_for_database(import_df)
        except DataValidationError as exc:
            st.sidebar.error(str(exc))
            import_df = None
        else:
            read_count = f"{len(import_df):,}".replace(",", ".")
            valid_count = f"{len(valid_preview):,}".replace(",", ".")
            if len(valid_preview) == len(import_df):
                st.sidebar.caption(f"{valid_count} baris siap ditambahkan")
            else:
                st.sidebar.caption(
                    f"{valid_count} baris valid dari {read_count} baris terbaca"
                )
            if valid_preview.empty:
                st.sidebar.warning("Tidak ada baris gempa valid pada pilihan ini.")
                import_df = None

    if import_df is not None and valid_preview is not None:
        with st.sidebar.expander("Pratinjau data", expanded=False):
            st.dataframe(
                valid_preview[DATABASE_COLUMNS].head(5),
                width="stretch",
                hide_index=True,
            )

    if st.sidebar.button(
        "Tambahkan ke database",
        type="primary",
        width="stretch",
        disabled=import_df is None or not mapping_complete,
    ):
        try:
            result = append_to_master_database(
                MASTER_DATA_PATH,
                import_df,
                seed_path=SEED_DATA_PATH,
            )
        except DataValidationError as exc:
            st.sidebar.error(str(exc))
            return
        except DatabaseWriteError as exc:
            st.sidebar.error(str(exc))
            return

        load_database_file.clear()
        load_working_store_summary.clear()
        load_working_store_query.clear()
        st.session_state.clean_df = None
        st.session_state.data_signature = None
        st.session_state.last_import_result = result
        st.rerun()


def render_publication_builder(
    filtered_df: pd.DataFrame,
    fault_geojson: dict[str, Any],
    trench_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
    district_geojson: dict[str, Any],
    slab_geojson: dict[str, Any] | None,
    population_density_geojson: dict[str, Any] | None,
    selected_provinces: list[str],
    selected_districts: list[str],
    visible_fault_indexes: list[int] | None,
    show_basemap: bool,
    show_faults: bool,
    show_trenches: bool,
    show_slabs: bool,
    show_population_density: bool,
    show_provinces: bool,
    show_region_labels: bool,
    basemap_name: str,
    show_shortbread_roads: bool,
    show_shortbread_rivers: bool,
    show_shortbread_topography: bool,
    show_stations: bool = False,
    station_records: list | None = None,
    show_geology: bool = False,
    geology_style_mode: str = GEOLOGY_STYLE_OFFICIAL,
    geology_units: list[dict[str, Any]] | None = None,
    geology_index_units: list[dict[str, Any]] | None = None,
    selected_geology_keys: list[str] | None = None,
) -> None:
    station_records = station_records or []
    geology_units = geology_units or []
    if geology_index_units is not None:
        geology_index_units = list(geology_index_units)
    selected_geology_keys = selected_geology_keys or []
    # Sudah berada di tab sendiri, jadi panel tidak perlu ditutup lagi.
    with st.expander("Buat Peta Publikasi", expanded=True):
        st.caption(
            "Peta aktif dicapture langsung setelah seluruh filter diterapkan."
        )
        has_earthquakes = not filtered_df.empty
        if selected_districts:
            region_label = ", ".join(selected_districts)
        elif selected_provinces:
            region_label = ", ".join(selected_provinces)
        else:
            region_label = "Indonesia"
        period_range = st.session_state.get("publication_date_range")
        if period_range:
            period_label = (
                f"{period_range[0]:%d %b %Y} - {period_range[1]:%d %b %Y}"
            )
        elif has_earthquakes:
            period_label = (
                f"{filtered_df['waktu'].min():%d %b %Y} - "
                f"{filtered_df['waktu'].max():%d %b %Y}"
            )
        else:
            period_label = None
        title_prefix = "Peta Episenter Gempabumi" if has_earthquakes else "Peta Wilayah"
        default_title = f"{title_prefix} {region_label}"
        if period_label:
            default_title += f" Periode {period_label}"
        default_title = default_title[:180]
        # Keep the title in step with the province/kabupaten and period filters
        # until the user types their own; a manual edit is detected as a value
        # that no longer matches the last auto-generated title, and preserved.
        if (
            "publication_title" not in st.session_state
            or st.session_state["publication_title"]
            == st.session_state.get("_publication_title_auto")
        ):
            st.session_state["publication_title"] = default_title
        st.session_state["_publication_title_auto"] = default_title

        title = st.text_area(
            "Judul peta",
            height=100,
            max_chars=180,
            key="publication_title",
            help="Gunakan Enter untuk membuat hingga tiga baris judul.",
        )
        institution_name = st.text_area(
            "Nama instansi",
            value="Nama Instansi",
            height=100,
            max_chars=140,
            key="publication_institution",
            help="Gunakan Enter untuk menambahkan unit atau kantor hingga tiga baris.",
        )
        logo_left_column, logo_right_column = st.columns(2)
        with logo_left_column:
            logo_file = st.file_uploader(
                "Logo 1",
                type=["png", "jpg", "jpeg", "webp"],
                key="publication_logo",
            )
        with logo_right_column:
            second_logo_file = st.file_uploader(
                "Logo 2",
                type=["png", "jpg", "jpeg", "webp"],
                key="publication_second_logo",
            )

        control_left, control_right = st.columns(2)
        with control_left:
            if st.session_state.get("_publication_template_geology_state") != show_geology:
                st.session_state["publication_template"] = (
                    PUBLICATION_TEMPLATE_BANDUNG
                    if show_geology
                    else PUBLICATION_TEMPLATE_LEGACY
                )
                st.session_state["_publication_template_geology_state"] = show_geology
            if st.session_state.get("publication_template") not in PUBLICATION_TEMPLATES:
                st.session_state["publication_template"] = (
                    PUBLICATION_TEMPLATE_BANDUNG
                    if show_geology
                    else PUBLICATION_TEMPLATE_LEGACY
                )
            publication_template = st.selectbox(
                "Template layout",
                options=list(PUBLICATION_TEMPLATES),
                key="publication_template",
                help=(
                    "Template geologi memperlebar panel legenda. Template Tata "
                    "Ruang (RTRW) memindahkan judul ke sidebar dan menambahkan "
                    "blok proyeksi/datum serta penanda tangan."
                ),
            )
            if publication_template == PUBLICATION_TEMPLATE_RTRW:
                map_type_label = st.text_input(
                    "Jenis peta",
                    value=(
                        "PETA GEOLOGI" if show_geology else "PETA SEBARAN GEMPA BUMI"
                    ),
                    key="publication_map_type",
                    help="Ditampilkan pada kotak tersendiri di bawah judul.",
                )
            else:
                map_type_label = ""
            page_size = st.selectbox(
                "Ukuran keluaran",
                options=list(PAGE_SIZES),
                key="publication_page_size",
            )
            resolution_label = st.selectbox(
                "Resolusi keluaran",
                options=list(PUBLICATION_DPI_OPTIONS),
                index=1,
                key="publication_output_resolution",
                help=(
                    "300 DPI direkomendasikan untuk cetak. Ukuran file dan waktu "
                    "pembuatan sekitar empat kali lebih besar daripada 144 DPI."
                ),
            )
            output_dpi = PUBLICATION_DPI_OPTIONS[resolution_label]
            scale_mode = st.selectbox(
                "Mode skala publikasi",
                options=("Otomatis sesuai cakupan", "Skala numerik"),
                key="publication_scale_mode",
                help=(
                    "Skala numerik mengubah cakupan peta secara nyata dengan "
                    "pusat tetap mengikuti wilayah atau data yang sedang difilter."
                ),
            )
            publication_scale_denominator: int | None = None
            if scale_mode == "Skala numerik":
                publication_scale_denominator = int(
                    st.number_input(
                        "Penyebut skala (1 : n)",
                        min_value=1_000,
                        max_value=100_000_000,
                        value=100_000,
                        step=10_000,
                        format="%d",
                        key="publication_scale_denominator",
                        help="Contoh: isi 100000 untuk menghasilkan skala 1 : 100.000.",
                    )
                )
                st.caption(
                    "Cakupan dipusatkan pada filter aktif. Objek di luar cakupan "
                    "skala pilihan tidak akan masuk ke peta publikasi."
                )
            logo_scale = st.slider(
                "Perbesaran logo",
                min_value=0,
                max_value=100,
                value=0,
                step=5,
                key="publication_logo_scale",
                help="0 = ukuran standar, 100 = logo dan area logo pada sidebar diperbesar hingga sekitar 3x. Nama instansi dan legenda otomatis bergeser turun mengikuti ukuran logo.",
            )
            marker_scale = st.slider(
                "Skala marker (%)",
                min_value=50,
                max_value=180,
                value=100,
                step=5,
                key="publication_marker_scale",
            )
        with control_right:
            if st.session_state.get("_publication_synced_basemap") != basemap_name:
                st.session_state.publication_basemap_name = basemap_name
                st.session_state._publication_synced_basemap = basemap_name
            publication_basemap_name = st.selectbox(
                "Basemap peta publikasi",
                options=BASEMAP_OPTIONS,
                key="publication_basemap_name",
            )
            publication_show_roads = False
            publication_show_rivers = True
            publication_show_topography = False
            publication_basemap_legend = False
            if publication_basemap_name == BASEMAP_SHORTBREAD:
                topography_default = show_shortbread_topography
                publication_show_topography = st.toggle(
                    "Tampilkan topografi",
                    value=topography_default,
                    key="publication_show_shortbread_topography_fast",
                )
                publication_show_roads = st.toggle(
                    "Tampilkan jalan",
                    value=show_shortbread_roads,
                    key="publication_show_shortbread_roads",
                )
                publication_show_rivers = st.toggle(
                    "Tampilkan sungai",
                    value=show_shortbread_rivers,
                    key="publication_show_shortbread_rivers",
                )
                publication_basemap_legend = st.toggle(
                    "Legenda basemap",
                    value=True,
                    key="publication_shortbread_legend",
                )
            map_padding = st.slider(
                "Ruang tepi peta (%)",
                min_value=5,
                max_value=40,
                value=12,
                step=1,
                key="publication_padding",
            )
            publication_basemap = st.toggle(
                "Tampilkan basemap",
                value=show_basemap,
                key="publication_basemap",
            )
            publication_earthquakes = st.toggle(
                "Layer titik gempa",
                value=True,
                key="publication_earthquakes",
                help=(
                    "Matikan untuk membuat peta tanpa episenter. Legenda "
                    "magnitudo dan kedalaman serta baris sumber data gempa "
                    "ikut disembunyikan, dan cakupan peta mengikuti wilayah "
                    "yang dipilih, bukan sebaran gempa."
                ),
            )
            publication_faults = st.toggle(
                "Layer sesar",
                value=show_faults,
                key="publication_faults",
            )
            publication_trenches = st.toggle(
                "Layer trench",
                value=show_trenches,
                key="publication_trenches",
            )
            publication_slabs = st.toggle(
                "Layer kontur slab",
                value=show_slabs and slab_geojson is not None,
                key="publication_slabs",
                disabled=slab_geojson is None,
            )
            publication_stations = st.toggle(
                "Layer stasiun seismik",
                value=show_stations and bool(station_records),
                key="publication_stations",
                disabled=not station_records,
            )
            has_population_density = bool(
                (population_density_geojson or {}).get("features")
            )
            publication_population_density = st.toggle(
                "Layer kepadatan penduduk",
                value=show_population_density,
                key="publication_population_density",
                disabled=not has_population_density,
            )
            publication_population_density_legend = st.toggle(
                "Legenda kepadatan penduduk",
                value=True,
                key="publication_population_density_legend",
                disabled=not publication_population_density,
            )
            publication_geology = show_geology and st.toggle(
                f"Layer geologi — {geology_style_mode}",
                value=show_geology,
                key="publication_geology",
                disabled=not show_geology,
                help=(
                    "Mengikuti filter unit/formasi pada sidebar. PDF menambahkan "
                    "indeks unit dan ringkasan atribut bila diperlukan."
                ),
            )
            publication_geology_source = st.radio(
                "Sumber gambar geologi",
                options=("Render resmi BIG", "Poligon lokal"),
                key="publication_geology_source",
                disabled=not publication_geology,
                help=(
                    "Render resmi BIG memakai simbolisasi server dan butuh "
                    "koneksi setiap kali peta dibuat. Poligon lokal mengunduh "
                    "geometrinya lalu menggambar sendiri: warna per formasi "
                    "terkendali dan label ditempatkan lokal, tetapi geometri "
                    "digeneralisasi dan hasilnya bukan render resmi BIG."
                ),
            )
            publication_provinces = st.toggle(
                "Batas provinsi",
                value=show_provinces,
                key="publication_provinces",
            )
            publication_region_labels = st.toggle(
                "Nama daerah pada peta",
                value=show_region_labels,
                key="publication_region_labels",
            )
            show_inset = st.toggle(
                "Peta inset Indonesia",
                value=True,
                key="publication_inset",
            )

        source_text = st.text_input(
            "Sumber data gempa",
            value=APP_DISPLAY_NAME,
            key="publication_source",
            help="Sumber layer lain ditambahkan otomatis sesuai layer yang aktif.",
        )
        # Resolved once so the snapshot, the sheet and the caption below all
        # size the overflow legend from an identical unit list.
        publication_geology_units = (
            list_visible_geology_units(
                geology_units,
                geology_index_units,
                catalog_known=geology_index_units is not None,
                selected_geology_keys=selected_geology_keys,
            )
            if publication_geology
            else []
        )
        publication_geology_labels = [
            unit.display_name for unit in publication_geology_units
        ]
        if publication_geology:
            index_scope = (
                ", ".join(selected_districts)
                if selected_districts
                else ", ".join(selected_provinces) or "Indonesia"
            )
            if geology_index_units is None:
                st.caption(
                    "Indeks PDF memakai legenda umur karena katalog unit pada "
                    f"cakupan {index_scope} belum tersedia."
                )
            else:
                effective_index_count = len(publication_geology_units)
                overflow_plan = plan_geology_legend_overflow(
                    page_size,
                    output_dpi,
                    publication_template,
                    unit_count=effective_index_count,
                    unit_labels=publication_geology_labels,
                )
                placements = [
                    f"{effective_index_count - overflow_plan.overflow_count} "
                    "pada legenda sidebar"
                ]
                if overflow_plan.capacity:
                    placements.append(
                        f"{min(overflow_plan.capacity, overflow_plan.overflow_count)} "
                        "pada blok legenda di samping peta"
                    )
                if overflow_plan.remainder:
                    placements.append(
                        f"{overflow_plan.remainder} pada indeks PDF lanjutan"
                    )
                st.caption(
                    f"{effective_index_count} unit/formasi beririsan dengan "
                    f"{index_scope}: " + ", ".join(placements) + "."
                )
        logo_bytes = logo_file.getvalue() if logo_file else b""
        second_logo_bytes = second_logo_file.getvalue() if second_logo_file else b""
        publication_hasher = hashlib.sha256()
        publication_row_hashes = pd.util.hash_pandas_object(
            filtered_df[["waktu", "lintang", "bujur", "kedalaman", "magnitudo"]],
            index=False,
        ).to_numpy()
        publication_hasher.update(publication_row_hashes.tobytes())
        publication_settings = "|".join(
            [
                "publication-layout-v16-local-geology-polygons",
                map_type_label,
                str(publication_earthquakes),
                publication_geology_source,
                publication_template,
                title,
                institution_name,
                page_size,
                str(output_dpi),
                scale_mode,
                str(publication_scale_denominator or "auto"),
                str(logo_scale),
                str(marker_scale),
                str(map_padding),
                str(publication_basemap),
                publication_basemap_name,
                str(publication_show_roads),
                str(publication_show_rivers),
                str(publication_show_topography),
                str(publication_basemap_legend),
                str(publication_faults),
                str(visible_fault_indexes is None),
                ",".join(str(index) for index in (visible_fault_indexes or [])),
                ",".join(sorted(selected_provinces)),
                ",".join(sorted(selected_districts)),
                str(publication_trenches),
                str(publication_slabs),
                str(len((slab_geojson or {}).get("features", []))),
                str(publication_stations),
                str(len(station_records)),
                str(publication_population_density),
                str(publication_population_density_legend),
                str(publication_geology),
                geology_style_mode,
                ",".join(selected_geology_keys),
                ",".join(sorted(str(unit.get("key") or "") for unit in geology_units)),
                (
                    "unknown"
                    if geology_index_units is None
                    else ",".join(sorted(
                        str(unit.get("key") or "")
                        for unit in geology_index_units
                    ))
                ),
                str(len((population_density_geojson or {}).get("features", []))),
                str(publication_provinces),
                str(publication_region_labels),
                str(show_inset),
                source_text,
            ]
        )
        publication_hasher.update(publication_settings.encode("utf-8"))
        publication_hasher.update(logo_bytes)
        publication_hasher.update(second_logo_bytes)
        publication_signature = publication_hasher.hexdigest()
        can_frame_region = bool(selected_provinces or selected_districts)
        build_disabled = not has_earthquakes and not can_frame_region
        if build_disabled:
            st.info(
                "Tidak ada titik gempa pada filter ini. Pilih provinsi atau "
                "kabupaten/kota pada filter di sidebar untuk membuat peta wilayah."
            )
        if st.button(
            "Ambil Snapshot & Buat Layout",
            type="primary",
            width="stretch",
            disabled=build_disabled,
        ):
            # Never leave an older result visible while a new publication is
            # still incomplete; downloads reappear only after every tile and
            # both requested outputs have been rendered successfully.
            st.session_state.publication_map_result = None
            publication_status = st.status(
                "Menyiapkan basemap publikasi...",
                expanded=True,
            )
            publication_progress = st.progress(
                0,
                text="Menghitung tile yang diperlukan...",
            )
            progress_phase = {"label": "Snapshot peta", "value": 0.0}

            def update_basemap_progress(
                message: str,
                completed: int,
                total: int,
            ) -> None:
                if total > 0:
                    progress_phase["value"] = max(
                        0.0,
                        min(1.0, completed / total),
                    )
                progress_text = f"{progress_phase['label']}: {message}"
                publication_progress.progress(
                    progress_phase["value"],
                    text=progress_text,
                )
                publication_status.update(
                    label=progress_text,
                    state="running",
                    expanded=True,
                )

            try:
                publication_geology_features: tuple[dict, ...] = ()
                if publication_geology and publication_geology_source == "Poligon lokal":
                    publication_status.update(
                        label="Mengunduh poligon geologi dari layanan BIG...",
                        state="running",
                        expanded=True,
                    )
                    try:
                        if selected_districts:
                            publication_geology_features = (
                                query_geology_features_for_region(
                                    district_geojson, selected_districts, "WADMKK"
                                )
                            )
                        else:
                            publication_geology_features = (
                                query_geology_features_for_region(
                                    province_geojson, selected_provinces, "PROVINSI"
                                )
                            )
                    except (OSError, ValueError) as exc:
                        raise PublicationMapError(
                            "Poligon geologi tidak dapat diunduh dari layanan BIG "
                            f"({exc}). Pilih 'Render resmi BIG' atau coba lagi."
                        ) from exc
                    if not publication_geology_features:
                        raise PublicationMapError(
                            "Layanan BIG tidak mengembalikan poligon geologi untuk "
                            "wilayah ini. Pilih 'Render resmi BIG'."
                        )
                map_snapshot = capture_filtered_map(
                    filtered_df,
                    fault_geojson,
                    province_geojson,
                    district_geojson=district_geojson,
                    trench_geojson=trench_geojson,
                    slab_geojson=slab_geojson,
                    population_density_geojson=population_density_geojson,
                    show_earthquakes=publication_earthquakes,
                    show_slabs=publication_slabs,
                    show_stations=publication_stations,
                    station_records=station_records,
                    show_population_density=publication_population_density,
                    show_region_labels=publication_region_labels,
                    output_size=get_publication_map_size(
                        page_size,
                        output_dpi,
                        publication_template,
                        # The snapshot has to be sized for the same map slot the
                        # sheet will have, including any overflow legend column.
                        geology_unit_count=len(publication_geology_units),
                        geology_unit_labels=publication_geology_labels,
                    ),
                    padding_percent=map_padding,
                    marker_scale_percent=marker_scale,
                    page_size=page_size,
                    output_dpi=output_dpi,
                    show_basemap=publication_basemap,
                    basemap_name=publication_basemap_name,
                    show_shortbread_roads=publication_show_roads,
                    show_shortbread_rivers=publication_show_rivers,
                    show_shortbread_topography=publication_show_topography,
                    show_faults=publication_faults,
                    show_trenches=publication_trenches,
                    show_provinces=publication_provinces,
                    fault_feature_indexes=visible_fault_indexes,
                    province_names=selected_provinces,
                    district_names=selected_districts,
                    show_geology=publication_geology,
                    geology_features=publication_geology_features,
                    geology_units=geology_units,
                    selected_geology_keys=selected_geology_keys,
                    geology_style_mode=geology_style_mode,
                    representative_scale_denominator=(
                        publication_scale_denominator
                    ),
                    template_name=publication_template,
                    basemap_progress_callback=update_basemap_progress,
                )
                progress_phase["label"] = "Layout publikasi"
                progress_phase["value"] = 0.0
                png_bytes, pdf_bytes = render_publication_map(
                    filtered_df,
                    fault_geojson,
                    province_geojson,
                    district_geojson=district_geojson,
                    trench_geojson=trench_geojson,
                    slab_geojson=slab_geojson,
                    population_density_geojson=population_density_geojson,
                    show_earthquakes=publication_earthquakes,
                    show_slabs=publication_slabs,
                    show_stations=publication_stations,
                    station_records=station_records,
                    show_population_density=publication_population_density,
                    show_population_density_legend=(
                        publication_population_density_legend
                    ),
                    title=title,
                    institution_name=institution_name,
                    logo_bytes=logo_bytes or None,
                    second_logo_bytes=second_logo_bytes or None,
                    logo_scale_percent=logo_scale,
                    marker_scale_percent=marker_scale,
                    padding_percent=map_padding,
                    page_size=page_size,
                    output_dpi=output_dpi,
                    show_basemap=publication_basemap,
                    basemap_name=publication_basemap_name,
                    show_shortbread_roads=publication_show_roads,
                    show_shortbread_rivers=publication_show_rivers,
                    show_shortbread_topography=publication_show_topography,
                    show_basemap_legend=publication_basemap_legend,
                    show_faults=publication_faults,
                    show_trenches=publication_trenches,
                    show_provinces=publication_provinces,
                    show_region_labels=publication_region_labels,
                    show_inset=show_inset,
                    fault_feature_indexes=visible_fault_indexes,
                    province_names=selected_provinces,
                    district_names=selected_districts,
                    source_text=source_text,
                    map_snapshot_bytes=map_snapshot,
                    map_type_label=map_type_label,
                    show_geology=publication_geology,
                    geology_features=publication_geology_features,
                    geology_units=geology_units,
                    geology_legend_units=geology_index_units,
                    geology_legend_catalog_known=(
                        geology_index_units is not None
                    ),
                    selected_geology_keys=selected_geology_keys,
                    geology_style_mode=geology_style_mode,
                    representative_scale_denominator=(
                        publication_scale_denominator
                    ),
                    template_name=publication_template,
                    basemap_progress_callback=update_basemap_progress,
                )
            except PublicationMapError as exc:
                publication_status.update(
                    label="Peta tidak dibuat karena basemap belum lengkap.",
                    state="error",
                    expanded=True,
                )
                st.error(str(exc))
            else:
                publication_progress.progress(
                    1.0,
                    text="Snapshot, layout PNG, dan PDF selesai dibuat.",
                )
                publication_status.update(
                    label="Peta publikasi selesai dan siap diunduh.",
                    state="complete",
                    expanded=False,
                )
                st.session_state.publication_map_result = {
                    "png": png_bytes,
                    "pdf": pdf_bytes,
                    "snapshot": map_snapshot,
                    "rows": len(filtered_df),
                    "title": title,
                    "signature": publication_signature,
                }

        result = st.session_state.publication_map_result
        if result is not None and result.get("signature") != publication_signature:
            st.info("Filter atau pengaturan berubah. Buat ulang peta untuk memperbarui preview.")
        elif result is not None:
            layout_tab, snapshot_tab = st.tabs(["Layout Publikasi", "Snapshot Peta"])
            with layout_tab:
                st.image(result["png"], width="stretch")
            with snapshot_tab:
                st.image(result["snapshot"], width="stretch")
            st.caption(
                f"Hasil terakhir menggunakan {result['rows']} kejadian terfilter."
            )
            download_filename = publication_filename_stem(result["title"])
            download_png, download_pdf, download_snapshot = st.columns(3)
            download_png.download_button(
                "Unduh PNG",
                data=result["png"],
                file_name=f"{download_filename}.png",
                mime="image/png",
                width="stretch",
            )
            download_pdf.download_button(
                "Unduh PDF",
                data=result["pdf"],
                file_name=f"{download_filename}.pdf",
                mime="application/pdf",
                width="stretch",
            )
            download_snapshot.download_button(
                "Unduh Snapshot",
                data=result["snapshot"],
                file_name=f"{download_filename}_Snapshot.png",
                mime="image/png",
                width="stretch",
            )


def format_depth(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.0f} km"


def count_near_fault(df: pd.DataFrame, radius_km: float = 10.0) -> int:
    """Count events whose nearest mapped fault trace lies within radius_km."""
    if "jarak_sesar_km" not in df.columns:
        return 0
    distances = pd.to_numeric(df["jarak_sesar_km"], errors="coerce")
    return int((distances <= radius_km).sum())


def render_statistics(filtered_df: pd.DataFrame) -> None:
    """Statistik tab: ringkasan angka dan empat grafik yang sebelumnya tidak ada."""
    if filtered_df.empty:
        st.info(
            "Belum ada kejadian pada filter saat ini, sehingga statistik tidak "
            "dapat dihitung. Longgarkan filter di sidebar terlebih dahulu."
        )
        return

    stats = calculate_summary_stats(filtered_df)
    depths = pd.to_numeric(filtered_df["kedalaman"], errors="coerce").dropna()
    summary_columns = st.columns(4)
    summary_columns[0].metric("Total kejadian", format_metric(stats["jumlah_gempa"]))
    summary_columns[1].metric(
        "Rata-rata M", format_metric(stats["magnitudo_rata_rata"])
    )
    summary_columns[2].metric("Kedalaman rata-rata", format_depth(depths.mean()))
    summary_columns[3].metric(
        "Dangkal (< 60 km)", format_metric(int((depths < 60).sum()))
    )

    top_row = st.columns(2)
    with top_row[0].container(border=True):
        st.markdown(
            '<div class="section-heading">Distribusi Kedalaman</div>',
            unsafe_allow_html=True,
        )
        if depths.empty:
            st.caption("Kolom kedalaman tidak memuat angka yang dapat dihitung.")
        else:
            bin_width = 25
            upper = int(np.ceil(max(depths.max(), bin_width) / bin_width) * bin_width)
            edges = np.arange(0, upper + bin_width, bin_width)
            counts, _ = np.histogram(depths, bins=edges)
            # Numeric index: string labels would be ordered alphabetically by the chart.
            depth_histogram = pd.DataFrame(
                {"Kedalaman (km)": edges[:-1].astype(int), "Kejadian": counts}
            ).set_index("Kedalaman (km)")
            st.bar_chart(depth_histogram, color="#0b5ea8", height=260)
            st.caption(
                f"Lebar bin {bin_width} km; sumbu mendatar menunjukkan batas bawah bin."
            )

    with top_row[1].container(border=True):
        st.markdown(
            '<div class="section-heading">Magnitudo terhadap Kedalaman</div>',
            unsafe_allow_html=True,
        )
        scatter_df = (
            filtered_df[["magnitudo", "kedalaman"]]
            .dropna()
            .rename(columns={"magnitudo": "Magnitudo", "kedalaman": "Kedalaman (km)"})
        )
        if scatter_df.empty:
            st.caption("Tidak ada pasangan magnitudo-kedalaman yang lengkap.")
        else:
            st.scatter_chart(
                scatter_df,
                x="Magnitudo",
                y="Kedalaman (km)",
                color="#0ea5e9",
                height=260,
            )
            st.caption(
                f"{len(scatter_df):,} titik.".replace(",", ".")
                + " Sumbu tegak adalah kedalaman hiposenter."
            )

    bottom_row = st.columns(2)
    with bottom_row[0].container(border=True):
        st.markdown(
            '<div class="section-heading">Provinsi Terbanyak</div>',
            unsafe_allow_html=True,
        )
        province_counts = (
            filtered_df["provinsi"].fillna("Tidak diketahui").value_counts().head(10)
        )
        province_table = province_counts.rename_axis("Provinsi").reset_index(
            name="Kejadian"
        )
        st.dataframe(
            province_table,
            width="stretch",
            hide_index=True,
            column_config={
                "Kejadian": st.column_config.ProgressColumn(
                    "Kejadian",
                    format="%d",
                    min_value=0,
                    max_value=int(province_counts.max()),
                )
            },
        )

    with bottom_row[1].container(border=True):
        st.markdown(
            '<div class="section-heading">Magnitudo Terbesar</div>',
            unsafe_allow_html=True,
        )
        largest = filtered_df.nlargest(10, "magnitudo")[
            ["waktu", "magnitudo", "kedalaman", "provinsi", "kabupaten_kota"]
        ].copy()
        largest["waktu"] = largest["waktu"].dt.strftime("%d %b %Y %H:%M")
        largest = largest.rename(
            columns={
                "waktu": "Waktu",
                "magnitudo": "M",
                "kedalaman": "Dalam km",
                "provinsi": "Provinsi",
                "kabupaten_kota": "Kabupaten/Kota",
            }
        )
        st.dataframe(largest, width="stretch", hide_index=True)


def render_event_table(filtered_df: pd.DataFrame) -> None:
    """Data Kejadian tab: tabel hasil filter beserta unduhan CSV."""
    if filtered_df.empty:
        st.info(
            "Tidak ada kejadian yang cocok dengan filter saat ini, sehingga tabel "
            "dan unduhan CSV kosong."
        )
        return

    if len(filtered_df) > TABLE_PREVIEW_LIMIT:
        st.caption(
            f"Menampilkan {TABLE_PREVIEW_LIMIT:,} kejadian terbaru dari "
            f"{len(filtered_df):,} hasil. Unduhan CSV tetap memuat seluruh data."
        )
    display_df = filtered_df.tail(TABLE_PREVIEW_LIMIT).copy()
    display_df["waktu"] = display_df["waktu"].dt.strftime("%Y-%m-%d %H:%M:%S")
    display_df = display_df[
        [
            "waktu",
            "lintang",
            "bujur",
            "kedalaman",
            "magnitudo",
            "provinsi",
            "kabupaten_kota",
            "jenis_lokasi",
            "jarak_darat_km",
            "sesar",
            "segmen_sesar",
            "jarak_sesar_km",
            "tanggal",
            "jam",
        ]
    ].rename(
        columns={
            "waktu": "Waktu",
            "lintang": "Lat",
            "bujur": "Lon",
            "kedalaman": "Dalam km",
            "magnitudo": "M",
            "provinsi": "Provinsi",
            "kabupaten_kota": "Kabupaten/Kota",
            "jenis_lokasi": "Lokasi",
            "jarak_darat_km": "Jarak darat km",
            "sesar": "Jejak sesar terdekat (geometris)",
            "segmen_sesar": "Segmen",
            "jarak_sesar_km": "Jarak horizontal episenter-jejak (km)",
            "tanggal": "Tanggal",
            "jam": "Jam",
        }
    )
    st.dataframe(display_df, width="stretch", hide_index=True)

    csv_data = filtered_df.to_csv(index=False).encode("utf-8")
    filename = f"gempa_filter_{date.today():%Y%m%d}.csv"
    st.download_button(
        "Unduh CSV",
        data=csv_data,
        file_name=filename,
        mime="text/csv",
        width="content",
    )


def render_results(
    filtered_df: pd.DataFrame,
    all_earthquakes_df: pd.DataFrame,
    fault_geojson: dict[str, Any],
    volcano_geojson: dict[str, Any],
    trench_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
    district_geojson: dict[str, Any],
    population_density_geojson: dict[str, Any] | None,
    slab_geojson: dict[str, Any] | None,
    selected_provinces: list[str],
    selected_districts: list[str],
    visible_fault_indexes: list[int] | None,
    selected_volcano_names: list[str],
    show_basemap: bool,
    basemap_name: str,
    show_shortbread_roads: bool,
    show_shortbread_rivers: bool,
    show_shortbread_topography: bool,
    show_earthquakes: bool,
    show_faults: bool,
    show_volcanoes: bool,
    show_trenches: bool,
    show_slabs: bool,
    show_provinces: bool,
    show_region_labels: bool,
    show_population_density: bool,
    selected_density_provinces: list[str],
    selected_density_regions: list[str],
    population_density_range: tuple[int, int],
    show_geology: bool,
    geology_style_mode: str,
    geology_units: list[dict[str, Any]],
    geology_index_units: list[dict[str, Any]] | None,
    selected_geology_keys: list[str],
) -> None:
    map_extent_df = all_earthquakes_df
    if selected_districts:
        district_extent = all_earthquakes_df[
            all_earthquakes_df["kabupaten_kota"].isin(selected_districts)
        ]
        if not district_extent.empty:
            map_extent_df = district_extent
    elif selected_provinces:
        provincial_extent = all_earthquakes_df[
            all_earthquakes_df["provinsi"].isin(selected_provinces)
        ]
        if not provincial_extent.empty:
            map_extent_df = provincial_extent

    show_stations = bool(st.session_state.get("show_stations", False))
    station_records = load_station_reference(
        *seismic_station_signature(STATION_DATA_PATH)
    )
    map_shell = build_indonesia_map(
        map_extent_df,
        fault_geojson=fault_geojson,
        volcano_geojson=volcano_geojson,
        trench_geojson=trench_geojson,
        slab_geojson=slab_geojson,
        show_basemap=show_basemap,
        basemap_name=basemap_name,
        show_shortbread_roads=show_shortbread_roads,
        show_shortbread_rivers=show_shortbread_rivers,
        show_shortbread_topography=show_shortbread_topography,
        show_earthquakes=show_earthquakes,
        show_faults=show_faults,
        show_volcanoes=show_volcanoes,
        show_trenches=show_trenches,
        show_slabs=show_slabs,
        show_stations=show_stations,
        station_records=station_records,
        fault_feature_indexes=visible_fault_indexes,
        volcano_names=(selected_volcano_names or None),
        province_geojson=province_geojson,
        district_geojson=district_geojson,
        population_density_geojson=population_density_geojson,
        show_population_density=show_population_density,
        show_provinces=show_provinces,
        province_names=selected_provinces,
        district_names=selected_districts,
        show_region_labels=show_region_labels,
        show_layer_control=False,
        include_geology_layer=show_geology,
        geology_initially_visible=show_geology,
        geology_units=geology_units,
        geology_selected_keys=selected_geology_keys,
        geology_style_mode=geology_style_mode,
        embed_earthquake_layer=False,
    )
    earthquake_layer = (
        build_earthquake_feature_group(filtered_df)
        if show_earthquakes and not filtered_df.empty
        else None
    )
    map_component_key = get_map_component_key(
        "earthquake-map-shell",
        map_extent_df,
        show_basemap,
        basemap_name,
        show_shortbread_roads,
        show_shortbread_rivers,
        show_shortbread_topography,
        show_earthquakes,
        show_faults,
        show_volcanoes,
        show_trenches,
        show_slabs,
        show_stations,
        len(station_records),
        len((slab_geojson or {}).get("features", [])),
        show_provinces,
        show_region_labels,
        visible_fault_indexes is None,
        tuple(visible_fault_indexes or []),
        tuple(selected_volcano_names),
        tuple(selected_provinces),
        tuple(selected_districts),
        show_population_density,
        tuple(selected_density_provinces),
        tuple(selected_density_regions),
        population_density_range,
        len((population_density_geojson or {}).get("features", [])),
        show_geology,
        geology_style_mode,
        tuple(selected_geology_keys),
        tuple(unit.get("key", "") for unit in geology_units),
    )

    map_tab, statistics_tab, table_tab, publication_tab = st.tabs(
        ["Peta Sebaran", "Statistik", "Data Kejadian", "Peta Publikasi"]
    )

    with map_tab:
        if filtered_df.empty:
            st.warning(
                "Tidak ada data gempa yang sedang ditampilkan. Ubah filter atau "
                "pulihkan data yang disembunyikan secara manual."
            )
        else:
            stats = calculate_summary_stats(filtered_df)
            metric_columns = st.columns(4)
            metric_columns[0].metric(
                "Total kejadian", format_metric(stats["jumlah_gempa"])
            )
            metric_columns[1].metric(
                "Magnitudo maks", format_metric(stats["magnitudo_maksimum"])
            )
            metric_columns[2].metric(
                "Kedalaman median",
                format_depth(
                    pd.to_numeric(filtered_df["kedalaman"], errors="coerce").median()
                ),
            )
            metric_columns[3].metric(
                "Dekat sesar (<= 10 km)", format_metric(count_near_fault(filtered_df))
            )

        map_card = st.container(border=True)
        with map_card:
            map_result = st_folium(
                map_shell,
                width=None,
                height=MAP_HEIGHT,
                returned_objects=["last_object_clicked_tooltip"],
                feature_group_to_add=earthquake_layer,
                layer_control=folium.LayerControl(collapsed=True),
                key=map_component_key,
            )
        map_action = (map_result or {}).get("last_object_clicked_tooltip")
        if (
            isinstance(map_action, str)
            and map_action.startswith("hide:eq_")
            and map_action != st.session_state.last_earthquake_map_action
        ):
            action_parts = map_action.split(":", 2)
            event_id = action_parts[1] if len(action_parts) == 3 else None
            if find_earthquake_by_event_id(all_earthquakes_df, event_id) is not None:
                st.session_state.last_earthquake_map_action = map_action
                hidden_ids = set(st.session_state.hidden_earthquake_ids)
                hidden_ids.add(event_id)
                st.session_state.hidden_earthquake_ids = sorted(hidden_ids)
                st.rerun()
        elif (
            show_volcanoes
            and isinstance(map_action, str)
            and map_action.startswith("Gunung api:")
        ):
            volcano_name = map_action.removeprefix("Gunung api:").strip()
            if (
                volcano_name in get_volcano_names(volcano_geojson)
                and selected_volcano_names != [volcano_name]
            ):
                st.session_state["_pending_volcano_selection"] = volcano_name
                st.rerun()

        if filtered_df.empty:
            st.caption(
                "Tidak ada titik gempa pada filter saat ini. Anda tetap dapat membuat "
                "peta wilayah dengan memilih provinsi atau kabupaten/kota pada filter "
                "di sidebar, lalu buka tab \"Peta Publikasi\"."
            )
        else:
            st.caption(
                "Sumber: PUSGEN 2024/InaRISK, BPS, BIG/Kebijakan Satu Peta, dan data "
                "trench lokal. "
                "Kepadatan memakai ordinary kriging untuk provinsi dan choropleth kecamatan untuk "
                "kabupaten/kota. Titik laut mengikuti provinsi terdekat (maks. 400 km). "
                "Jarak sesar dan gunung api adalah jarak permukaan dan tidak menentukan penyebab."
            )

    with statistics_tab:
        render_statistics(filtered_df)

    with table_tab:
        with st.container(border=True):
            render_event_table(filtered_df)

    with publication_tab:
        render_publication_builder(
            filtered_df,
            fault_geojson,
            trench_geojson,
            province_geojson,
            district_geojson,
            slab_geojson,
            population_density_geojson,
            selected_provinces,
            selected_districts,
            visible_fault_indexes,
            show_basemap,
            show_faults,
            show_trenches,
            show_slabs,
            show_population_density,
            show_provinces,
            show_region_labels,
            basemap_name,
            show_shortbread_roads,
            show_shortbread_rivers,
            show_shortbread_topography,
            show_stations,
            station_records,
            show_geology,
            geology_style_mode,
            geology_units,
            geology_index_units,
            selected_geology_keys,
        )

    st.markdown(
        f'<div class="app-footer">&copy; 2026 {APP_DISPLAY_NAME}</div>',
        unsafe_allow_html=True,
    )


def main() -> None:
    initialize_state()
    apply_theme()

    st.sidebar.title("Database Gempa")
    render_import_panel()
    try:
        fault_geojson = load_fault_reference(
            str(FAULT_DATA_PATH),
            FAULT_DATA_PATH.stat().st_mtime_ns,
        )
    except (FaultDataError, OSError) as exc:
        st.error(str(exc))
        st.stop()

    try:
        volcano_geojson = load_volcano_reference(
            str(VOLCANO_DATA_PATH),
            VOLCANO_DATA_PATH.stat().st_mtime_ns,
        )
    except (VolcanoDataError, OSError) as exc:
        st.error(str(exc))
        st.stop()

    try:
        trench_geojson = load_trench_reference(
            str(TRENCH_DATA_PATH),
            TRENCH_DATA_PATH.stat().st_mtime_ns,
        )
    except (TrenchDataError, OSError) as exc:
        st.error(str(exc))
        st.stop()

    slab_geojson: dict[str, Any] | None = None
    if SLAB_DATA_PATH.exists():
        try:
            slab_geojson = load_slab_reference(
                str(SLAB_DATA_PATH),
                SLAB_DATA_PATH.stat().st_mtime_ns,
            )
        except (SlabDataError, OSError) as exc:
            st.sidebar.warning(f"Kontur slab tidak dimuat: {exc}")

    try:
        province_geojson = load_region_reference(
            str(PROVINCE_DATA_PATH),
            PROVINCE_DATA_PATH.stat().st_mtime_ns,
        )
        district_geojson = load_region_reference(
            str(DISTRICT_DATA_PATH),
            DISTRICT_DATA_PATH.stat().st_mtime_ns,
        )
        coastal_regions = load_coastal_reference(
            str(COASTAL_DATA_PATH),
            COASTAL_DATA_PATH.stat().st_mtime_ns,
        )
        population_density_df, population_density_geojson = (
            load_population_density_map_reference(
                str(POPULATION_DENSITY_DATA_PATH),
                POPULATION_DENSITY_DATA_PATH.stat().st_mtime_ns,
                str(SUBDISTRICT_DATA_PATH),
                SUBDISTRICT_DATA_PATH.stat().st_mtime_ns,
            )
        )
    except (RegionDataError, CoastalDataError, OSError) as exc:
        st.error(str(exc))
        st.stop()
    except PopulationDensityDataError as exc:
        st.error(str(exc))
        st.stop()

    try:
        fault_province_index = load_fault_province_reference(
            str(FAULT_DATA_PATH),
            FAULT_DATA_PATH.stat().st_mtime_ns,
            str(PROVINCE_DATA_PATH),
            PROVINCE_DATA_PATH.stat().st_mtime_ns,
        )
    except (FaultDataError, RegionDataError, OSError) as exc:
        st.error(str(exc))
        st.stop()
    all_fault_names = get_fault_names(fault_geojson)
    all_volcano_names = get_volcano_names(volcano_geojson)
    fault_names_by_index = {
        feature_index: str(name)
        for feature_index, feature in enumerate(fault_geojson.get("features", []))
        if (name := feature.get("properties", {}).get("Name"))
    }
    all_province_names = sorted(
        {
            str(name)
            for feature in province_geojson.get("features", [])
            if (name := feature.get("properties", {}).get("PROVINSI"))
        }
    )

    archive_paths = get_archive_data_paths()
    reference_paths = (
        FAULT_DATA_PATH,
        PROVINCE_DATA_PATH,
        DISTRICT_DATA_PATH,
    )
    store_status = st.empty()
    try:
        rebuilt = ensure_working_database(
            WORKING_DATABASE_PATH,
            archive_paths,
            reference_paths,
            fault_geojson,
            province_geojson,
            district_geojson,
            progress=lambda message: store_status.info(message),
        )
    except EarthquakeStoreError as exc:
        store_status.empty()
        st.error(str(exc))
        st.stop()
    store_status.empty()
    if rebuilt:
        load_working_store_summary.clear()
        load_working_store_query.clear()
        st.sidebar.success("Indeks kerja SQLite berhasil diperbarui.")

    database_signature = working_database_signature(WORKING_DATABASE_PATH)
    try:
        store_summary = load_working_store_summary(*database_signature)
    except EarthquakeStoreError as exc:
        st.error(str(exc))
        st.stop()
    query_dates = render_catalog_date_query(store_summary, database_signature)
    if query_dates is None:
        st.stop()
    try:
        raw_df = load_working_store_query(
            *database_signature,
            query_dates[0],
            query_dates[1],
        )
    except EarthquakeStoreError as exc:
        st.error(str(exc))
        st.stop()

    data_signature = (
        f"sqlite:{database_signature[0]}:{database_signature[1]}:"
        f"{query_dates[0].isoformat()}:{query_dates[1].isoformat()}"
    )
    if st.session_state.data_signature != data_signature:
        st.session_state.data_signature = data_signature
        st.session_state.last_earthquake_map_action = None
        clear_hidden_earthquakes()

    clean_df = raw_df
    if clean_df is None or clean_df.empty:
        st.warning("Tidak ada data valid setelah proses cleaning.")
        st_folium(
            build_indonesia_map(
                fault_geojson=fault_geojson,
                volcano_geojson=volcano_geojson,
                trench_geojson=trench_geojson,
                province_geojson=province_geojson,
                include_geology_layer=False,
            ),
            width=None,
            height=700,
            returned_objects=[],
        )
        return

    (
        filtered_df,
        selected_provinces,
        selected_districts,
        visible_fault_indexes,
        show_basemap,
        basemap_name,
        show_shortbread_roads,
        show_shortbread_rivers,
        show_shortbread_topography,
        show_earthquakes,
        show_faults,
        selected_volcano_names,
        show_volcanoes,
        show_trenches,
        show_provinces,
        show_region_labels,
        show_slabs,
        slab_interval_km,
        show_population_density,
        selected_density_provinces,
        selected_density_regions,
        population_density_range,
        show_geology,
        geology_style_mode,
        geology_units,
        geology_index_units,
        selected_geology_keys,
    ) = render_sidebar_filters(
        clean_df,
        population_density_df,
        province_geojson,
        district_geojson,
        fault_province_index,
        all_fault_names,
        fault_names_by_index,
        all_province_names,
        fault_geojson,
        all_volcano_names,
        volcano_geojson,
        coastal_regions,
        data_signature,
        query_dates,
    )
    filtered_df = filter_hidden_earthquakes(
        filtered_df,
        st.session_state.hidden_earthquake_ids,
    )
    hidden_count = len(st.session_state.hidden_earthquake_ids)
    if hidden_count:
        st.sidebar.caption(
            f"{hidden_count:,} kejadian disembunyikan secara manual.".replace(",", ".")
        )
        st.sidebar.button(
            "Tampilkan kembali semua gempa",
            on_click=clear_hidden_earthquakes,
            width="stretch",
        )

    visible_population_density_geojson = filter_population_density_geojson(
        population_density_geojson,
        region_names=selected_density_regions,
        density_range=population_density_range,
        province_names=selected_density_provinces,
    )
    visible_slab_geojson: dict[str, Any] | None = None
    if slab_geojson is not None and show_slabs:
        visible_slab_geojson = filter_slab_contours(
            slab_geojson,
            interval_km=slab_interval_km,
        )
    render_page_header(
        f"gempa_working.sqlite &middot; {len(clean_df):,} baris".replace(",", ".")
    )
    render_results(
        filtered_df,
        clean_df,
        fault_geojson,
        volcano_geojson,
        trench_geojson,
        province_geojson,
        district_geojson,
        visible_population_density_geojson,
        visible_slab_geojson,
        selected_provinces,
        selected_districts,
        visible_fault_indexes,
        selected_volcano_names,
        show_basemap,
        basemap_name,
        show_shortbread_roads,
        show_shortbread_rivers,
        show_shortbread_topography,
        show_earthquakes,
        show_faults,
        show_volcanoes,
        show_trenches,
        show_slabs,
        show_provinces,
        show_region_labels,
        show_population_density,
        selected_density_provinces,
        selected_density_regions,
        population_density_range,
        show_geology,
        geology_style_mode,
        geology_units,
        geology_index_units,
        selected_geology_keys,
    )


if __name__ == "__main__":
    main()
