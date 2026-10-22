from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
import hashlib
from io import BytesIO
import math
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Collection, Iterable
import urllib.error
import urllib.parse
import urllib.request

import mapbox_vector_tile
import numpy as np
import pandas as pd
from PIL import Image, ImageColor, ImageDraw, ImageFont, UnidentifiedImageError

from utils.map_builder import (
    BASEMAP_CYCLOSM,
    BASEMAP_HUMANITARIAN,
    BASEMAP_OPENTOPO,
    BASEMAP_RELIEF,
    BASEMAP_SHORTBREAD,
    POPULATION_DENSITY_COLORS,
    POPULATION_DENSITY_DISTRICT_COLORS,
    SEISMIC_STATION_BORDER,
    SEISMIC_STATION_COLOR,
    SLAB_CONTOUR_COLOR,
    build_population_density_surfaces,
    classify_fault_type,
    get_basemap_attribution,
    get_depth_style,
    get_magnitude_marker,
    get_population_density_breaks,
    get_population_density_color,
    get_population_density_legend_labels,
    get_scaled_magnitude_radius,
    get_fault_label_records,
    get_fault_symbol_side,
    get_strike_slip_sense,
    _largest_outer_ring,
    _ring_centroid,
)


PAGE_SIZES = {
    "A4 Lanskap": (1684, 1191),
    "A3 Lanskap": (2382, 1684),
    "A2 Lanskap": (3368, 2382),
}
PUBLICATION_TEMPLATE_LEGACY = "Format Lama"
PUBLICATION_TEMPLATE_BANDUNG = "Peta Geologi Lembar Bandung"
PUBLICATION_TEMPLATE_RTRW = "Tata Ruang (RTRW)"
PUBLICATION_TEMPLATES = {
    PUBLICATION_TEMPLATE_LEGACY: 0.30,
    PUBLICATION_TEMPLATE_BANDUNG: 0.42,
    PUBLICATION_TEMPLATE_RTRW: 0.30,
}
PUBLICATION_HEADER_FILL = "#ffffff"
PUBLICATION_HEADER_TEXT = "#111111"
OUTPUT_DPI = 144.0
PUBLICATION_DPI_OPTIONS = {
    "Standar (144 DPI)": OUTPUT_DPI,
    "Cetak (300 DPI)": 300.0,
}
TILE_DOWNLOAD_ATTEMPTS = 1
TILE_DOWNLOAD_TIMEOUT_SECONDS = 8
TILE_DOWNLOAD_WORKERS = 4
MAX_RASTER_TILE_BYTES = 5_000_000
MAX_VECTOR_TILE_BYTES = 10_000_000
BASEMAP_COMPLETION_TIMEOUT_SECONDS = 120.0
BASEMAP_COMPLETION_MAX_ROUNDS = 12
BASEMAP_RETRY_DELAY_SECONDS = 2.5
DEFAULT_RETRY_AFTER_SECONDS = 3.0
PUBLICATION_TILE_OVERSAMPLE_FACTOR = 1.15
MAX_PUBLICATION_TILE_COUNT = 24
MAX_HIGH_RES_PUBLICATION_TILE_COUNT = 192
REFERENCE_PUBLICATION_MAP_PIXELS = 1_000 * 650
BasemapProgressCallback = Callable[[str, int, int], None]
# Upper bound on how much the logo may grow at the maximum slider value (100),
# expressed as extra multiples of its base footprint. The locator inset is
# protected separately by an adaptive cap (see _sidebar_legend_block_height),
# so this only limits the logo when the sidebar has ample free space.
LOGO_MAX_GROWTH = 2.0
FONT_DIR = Path("C:/Windows/Fonts")


def publication_filename_stem(title: str) -> str:
    """Convert a publication title into a safe underscore-separated filename."""
    filename = re.sub(r"[^\w]+", "_", str(title).strip(), flags=re.UNICODE)
    filename = re.sub(r"_+", "_", filename).strip("_")
    return filename or "Peta_Publikasi"
COORDINATE_FRAME_COLOR = "#102a72"


class PublicationMapError(ValueError):
    """Raised when a publication map cannot be rendered."""


@dataclass(frozen=True)
class PublicationHazardLayer:
    """Classified PSHA grid drawn beneath the tectonic lines of the sheet.

    ``classes`` is a north-row-first integer grid (-1 = no computation site)
    whose outer cell edges are ``bounds`` (west, south, east, north).
    """

    classes: Any
    bounds: tuple[float, float, float, float]
    colors: tuple[str, ...]
    labels: tuple[str, ...]
    heading: str
    source: str


class TileRetryAfterError(PublicationMapError):
    """Raised when a tile endpoint asks this tile to wait before retrying."""

    def __init__(self, message: str, retry_after_seconds: float) -> None:
        super().__init__(message)
        self.retry_after_seconds = max(0.0, float(retry_after_seconds))


class IncompleteBasemapError(PublicationMapError):
    """Carry exact persisted-tile progress between retry rounds."""

    def __init__(self, message: str, *, completed: int, total: int) -> None:
        super().__init__(message)
        self.completed = max(0, int(completed))
        self.total = max(self.completed, int(total))


def _publication_layout(
    page_size: str,
    output_dpi: float = OUTPUT_DPI,
    template_name: str = PUBLICATION_TEMPLATE_LEGACY,
) -> tuple[
    float,
    int,
    int,
    tuple[int, int, int, int],
    tuple[int, int, int, int],
]:
    """Split the sheet into header, map, and sidebar."""
    if page_size not in PAGE_SIZES:
        raise PublicationMapError("Ukuran keluaran tidak didukung.")
    page_width, page_height = get_publication_page_size(page_size, output_dpi)
    if template_name not in PUBLICATION_TEMPLATES:
        raise PublicationMapError("Template publikasi tidak didukung.")
    scale = page_width / PAGE_SIZES["A4 Lanskap"][0]
    # The RTRW sheet carries its title inside the sidebar, the way an official
    # tata-ruang sheet does, so the page needs no title band across the top.
    header_height = int(
        (18 if template_name == PUBLICATION_TEMPLATE_RTRW else 150) * scale
    )
    margin = int(38 * scale)
    gutter = int(26 * scale)
    sidebar_width = int(page_width * PUBLICATION_TEMPLATES[template_name])
    content_top = header_height + margin
    content_height = page_height - content_top - margin
    map_slot_width = page_width - sidebar_width - margin * 2 - gutter
    # Reserve a clean white collar for coordinate labels on all four sides.
    # The map image itself stays inside this collar, so the labels never cover
    # earthquake markers or compete with the sidebar.
    coordinate_margin = max(34, int(38 * scale))
    map_slot_left = margin
    map_box = (
        map_slot_left + coordinate_margin,
        content_top + coordinate_margin,
        map_slot_width - coordinate_margin * 2,
        content_height - coordinate_margin * 2,
    )
    sidebar_box = (
        map_slot_left + map_slot_width + gutter,
        content_top,
        sidebar_width,
        content_height,
    )
    return scale, header_height, margin, map_box, sidebar_box


def get_publication_page_size(
    page_size: str,
    output_dpi: float = OUTPUT_DPI,
) -> tuple[int, int]:
    """Return output pixels while keeping the selected paper size unchanged."""
    if page_size not in PAGE_SIZES:
        raise PublicationMapError("Ukuran keluaran tidak didukung.")
    if not math.isfinite(output_dpi) or output_dpi <= 0:
        raise PublicationMapError("Resolusi keluaran harus lebih besar dari 0 DPI.")
    scale = float(output_dpi) / OUTPUT_DPI
    return tuple(max(1, round(dimension * scale)) for dimension in PAGE_SIZES[page_size])


def get_publication_map_size(
    page_size: str,
    output_dpi: float = OUTPUT_DPI,
    template_name: str = PUBLICATION_TEMPLATE_LEGACY,
) -> tuple[int, int]:
    """Return the map pixels of the sheet's map slot."""
    _, _, _, map_box, _ = _publication_layout(page_size, output_dpi, template_name)
    return map_box[2], map_box[3]


def _publication_tile_limit(
    output_dpi: float,
    map_size: tuple[int, int] | None = None,
) -> int:
    """Scale the tile budget with pixel area for genuinely sharper basemaps."""
    dpi_scale = max(1.0, float(output_dpi) / OUTPUT_DPI)
    area_scale = 1.0
    if map_size is not None:
        width, height = int(map_size[0]), int(map_size[1])
        area_scale = max(1.0, width * height / REFERENCE_PUBLICATION_MAP_PIXELS)
    desired = round(MAX_PUBLICATION_TILE_COUNT * max(dpi_scale**2, area_scale))
    return min(
        MAX_HIGH_RES_PUBLICATION_TILE_COUNT,
        max(MAX_PUBLICATION_TILE_COUNT, desired),
    )


def _publication_magnitude_radius(magnitude: float, marker_scale: float) -> float:
    """Return the exact plotted marker radius, shared by markers and legend.

    Uses the same value the map snapshot and the dashboard plot, so a legend
    circle is the same size as the markers it describes instead of a floored
    approximation (which made M2 legend circles ~11% smaller than the markers).
    """
    return get_scaled_magnitude_radius(magnitude, marker_scale)


def _sidebar_legend_block_height(
    scale: float,
    marker_scale: float,
    institution_block_height: int,
    *,
    show_basemap: bool,
    basemap_name: str,
    show_basemap_legend: bool,
    show_shortbread_roads: bool,
    show_shortbread_rivers: bool,
    show_shortbread_topography: bool,
    show_trenches: bool,
    trench_present: bool,
    show_faults: bool,
    show_slabs: bool,
    slab_present: bool,
    show_provinces: bool,
    show_population_density: bool,
    show_population_density_legend: bool,
    show_stations: bool = False,
    density_color_count: int = len(POPULATION_DENSITY_COLORS),
    compact: bool = False,
    has_earthquakes: bool = True,
    hazard_class_count: int = 0,
) -> int:
    """Height in pixels from the institution text down to the locator inset.

    Mirrors the vertical increments used when the sidebar is drawn so the
    logo can be capped adaptively: whatever legend layers are switched on,
    the locator inset below keeps enough height for a sensible zoom.
    """
    height = institution_block_height  # institution text region -> scale bar
    height += int(36 * scale)  # scale bar region -> "Legenda" heading
    height += int(38 * scale)  # after the "Legenda" heading
    if show_basemap and basemap_name == BASEMAP_SHORTBREAD and show_basemap_legend:
        height += int(26 * scale)  # land / water swatches
        if show_shortbread_roads or show_shortbread_rivers:
            height += int(26 * scale)
        if show_shortbread_topography:
            height += int(26 * scale)
    tectonic_rows = 0
    if show_trenches and trench_present:
        tectonic_rows += 1
    if show_faults:
        tectonic_rows += 3
    # Compact mode shrinks the tectonic/slab/province rows so the magnitude
    # circles can stay at their true, marker-matching size below.
    tectonic_row_height = int((20 if compact else 25) * scale)
    height += tectonic_rows * tectonic_row_height
    if show_slabs and slab_present:
        height += tectonic_row_height
    if show_provinces:
        height += tectonic_row_height
    if show_stations:
        height += tectonic_row_height
    if show_population_density and show_population_density_legend:
        columns = 3 if compact else 2
        row_height = int((18 if compact else 24) * scale)
        density_rows = math.ceil(density_color_count / columns)
        height += (
            int((5 if compact else 8) * scale)
            + int((22 if compact else 27) * scale)
            + int(density_rows * row_height + (4 if compact else 6) * scale)
        )
    if hazard_class_count:
        columns = 3 if compact else 2
        row_height = int((18 if compact else 24) * scale)
        height += (
            int((5 if compact else 8) * scale)
            + int((22 if compact else 27) * scale)
            + int(math.ceil(hazard_class_count / columns) * row_height
                  + (4 if compact else 6) * scale)
        )
    if has_earthquakes:
        # Magnitude circles stay at their true (marker-matching) radius in both
        # modes; only the surrounding fonts/spacing compact.
        maximum_radius = math.ceil(_publication_magnitude_radius(8.0, marker_scale))
        if compact:
            height += int(5 * scale) + int(20 * scale) + 2 * maximum_radius + int(18 * scale)
            height += int(20 * scale) + int(58 * scale)  # depth legend
        else:
            height += int(8 * scale) + int(25 * scale) + 2 * maximum_radius + int(26 * scale)
            height += int(26 * scale) + int(76 * scale)  # depth legend
    return height


def _should_compact_sidebar_legends(
    *,
    side_y: int,
    side_height: int,
    scale: float,
    logo_top: int,
    base_logo_area_height: int,
    normal_legend_block_height: int,
) -> bool:
    """Compact crowded legends before they squeeze the locator inset."""
    source_y = side_y + side_height - int(150 * scale)
    projected_inset_top = (
        logo_top
        + base_logo_area_height
        + int(10 * scale)
        + normal_legend_block_height
        + int(6 * scale)
    )
    locator_heading_height = int(20 * scale)
    available_locator_height = (
        source_y
        - int(14 * scale)
        - projected_inset_top
        - locator_heading_height
    )
    return available_locator_height < int(110 * scale)


def capture_filtered_map(
    df: pd.DataFrame,
    fault_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
    *,
    district_geojson: dict[str, Any] | None = None,
    trench_geojson: dict[str, Any] | None = None,
    slab_geojson: dict[str, Any] | None = None,
    population_density_geojson: dict[str, Any] | None = None,
    hazard_layer: PublicationHazardLayer | None = None,
    output_size: tuple[int, int],
    padding_percent: int,
    marker_scale_percent: int,
    page_size: str = "A4 Lanskap",
    output_dpi: float = OUTPUT_DPI,
    show_basemap: bool,
    basemap_name: str = BASEMAP_OPENTOPO,
    show_shortbread_roads: bool = False,
    show_shortbread_rivers: bool = True,
    show_shortbread_topography: bool = False,
    show_faults: bool,
    show_earthquakes: bool = True,
    show_trenches: bool = True,
    show_slabs: bool = False,
    show_stations: bool = False,
    station_records: Collection[tuple[Any, ...]] | None = None,
    show_population_density: bool = False,
    show_provinces: bool,
    show_region_labels: bool = True,
    fault_names: Collection[str] | None = None,
    province_names: Collection[str] | None = None,
    district_names: Collection[str] | None = None,
    fault_feature_indexes: Collection[int] | None = None,
    basemap_progress_callback: BasemapProgressCallback | None = None,
    representative_scale_denominator: int | None = None,
    template_name: str = PUBLICATION_TEMPLATE_LEGACY,
    selection_bounds: tuple[float, float, float, float] | None = None,
) -> bytes:
    """Render a deterministic map snapshot without launching Edge/Chrome.

    The compiled desktop application has no console and previously delegated
    this work to a second browser process.  Rendering through the same Pillow
    publication canvas removes that fragile process boundary and behaves the
    same under ``streamlit run`` and the Nuitka EXE.
    """
    snapshot, _ = render_publication_map(
        df,
        fault_geojson,
        province_geojson,
        district_geojson=district_geojson,
        trench_geojson=trench_geojson,
        slab_geojson=slab_geojson,
        population_density_geojson=population_density_geojson,
        hazard_layer=hazard_layer,
        title="Snapshot Peta",
        selection_bounds=selection_bounds,
        institution_name="",
        marker_scale_percent=marker_scale_percent,
        padding_percent=padding_percent,
        page_size=page_size,
        output_dpi=output_dpi,
        show_basemap=show_basemap,
        basemap_name=basemap_name,
        show_shortbread_roads=show_shortbread_roads,
        show_shortbread_rivers=show_shortbread_rivers,
        show_shortbread_topography=show_shortbread_topography,
        show_basemap_legend=False,
        show_faults=show_faults,
        show_earthquakes=show_earthquakes,
        show_trenches=show_trenches,
        show_slabs=show_slabs,
        show_stations=show_stations,
        station_records=station_records,
        show_population_density=show_population_density,
        show_population_density_legend=False,
        show_provinces=show_provinces,
        show_region_labels=show_region_labels,
        show_inset=False,
        fault_names=fault_names,
        fault_feature_indexes=fault_feature_indexes,
        province_names=province_names,
        district_names=district_names,
        representative_scale_denominator=representative_scale_denominator,
        template_name=template_name,
        source_text="",
        _map_only_size=output_size,
        basemap_progress_callback=basemap_progress_callback,
    )
    return snapshot


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        [FONT_DIR / "arialbd.ttf", "DejaVuSans-Bold.ttf"]
        if bold
        else [FONT_DIR / "arial.ttf", "DejaVuSans.ttf"]
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _serif_font(
    size: int,
    *,
    bold: bool = False,
    italic: bool = False,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    windows_name = (
        "timesbi.ttf" if bold and italic else
        "timesbd.ttf" if bold else
        "timesi.ttf" if italic else
        "times.ttf"
    )
    fallback = (
        "DejaVuSerif-BoldItalic.ttf" if bold and italic else
        "DejaVuSerif-Bold.ttf" if bold else
        "DejaVuSerif-Italic.ttf" if italic else
        "DejaVuSerif.ttf"
    )
    for candidate in (FONT_DIR / windows_name, fallback):
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _ellipsize_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
) -> str:
    if draw.textlength(value, font=font) <= max_width:
        return value
    suffix = "…"
    candidate = value
    while candidate and draw.textlength(candidate + suffix, font=font) > max_width:
        candidate = candidate[:-1]
    return candidate.rstrip() + suffix if candidate else suffix


def _mercator_pixel(longitude: float, latitude: float, zoom: int) -> tuple[float, float]:
    latitude = max(-85.05112878, min(85.05112878, latitude))
    world_size = 256.0 * (2**zoom)
    x = (longitude + 180.0) / 360.0 * world_size
    latitude_radians = math.radians(latitude)
    y = (
        1.0
        - math.asinh(math.tan(latitude_radians)) / math.pi
    ) / 2.0 * world_size
    return x, y


def _inverse_mercator_pixel(x: float, y: float, zoom: int) -> tuple[float, float]:
    """Convert a Web Mercator world pixel back to longitude/latitude."""
    world_size = 256.0 * (2**zoom)
    longitude = x / world_size * 360.0 - 180.0
    mercator_y = math.pi * (1.0 - 2.0 * y / world_size)
    latitude = math.degrees(math.atan(math.sinh(mercator_y)))
    return longitude, latitude


def _fit_bounds_to_web_mercator_aspect(
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
) -> tuple[float, float, float, float]:
    """Expand bounds to the output aspect ratio in Web Mercator space.

    Leaflet preserves its viewport aspect ratio when fitting bounds.  The scale
    calculation must use the same expanded extent, not merely the data bounds.
    """
    width, height = size
    if width <= 0 or height <= 0:
        raise PublicationMapError("Ukuran bidang peta harus lebih besar dari nol.")

    west, south, east, north = bounds
    left, top = _mercator_pixel(west, north, 0)
    right, bottom = _mercator_pixel(east, south, 0)
    projected_width = right - left
    projected_height = bottom - top
    if projected_width <= 0 or projected_height <= 0:
        raise PublicationMapError("Cakupan koordinat peta tidak valid.")

    target_aspect = width / height
    projected_aspect = projected_width / projected_height
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    if projected_aspect < target_aspect:
        projected_width = projected_height * target_aspect
    else:
        projected_height = projected_width / target_aspect

    left = center_x - projected_width / 2.0
    right = center_x + projected_width / 2.0
    top = center_y - projected_height / 2.0
    bottom = center_y + projected_height / 2.0
    fitted_west, fitted_north = _inverse_mercator_pixel(left, top, 0)
    fitted_east, fitted_south = _inverse_mercator_pixel(right, bottom, 0)
    return fitted_west, fitted_south, fitted_east, fitted_north


def _map_bounds(df: pd.DataFrame, padding_percent: int) -> tuple[float, float, float, float]:
    west = float(df["bujur"].min())
    east = float(df["bujur"].max())
    south = float(df["lintang"].min())
    north = float(df["lintang"].max())
    longitude_span = max(0.35, east - west)
    latitude_span = max(0.28, north - south)
    longitude_center = (west + east) / 2.0
    latitude_center = (south + north) / 2.0
    padding_factor = 1.0 + (padding_percent / 100.0) * 2.0
    longitude_span *= padding_factor
    latitude_span *= padding_factor
    return (
        longitude_center - longitude_span / 2.0,
        latitude_center - latitude_span / 2.0,
        longitude_center + longitude_span / 2.0,
        latitude_center + latitude_span / 2.0,
    )


def _selection_frame_bounds(
    selection_bounds: tuple[float, float, float, float],
    padding_percent: int,
) -> tuple[float, float, float, float]:
    """Frame a user-drawn selection box with the same padding as the data."""
    west, south, east, north = selection_bounds
    padding_factor = 1.0 + (padding_percent / 100.0) * 2.0
    longitude_span = max(0.05, east - west) * padding_factor
    latitude_span = max(0.05, north - south) * padding_factor
    longitude_center = (west + east) / 2.0
    latitude_center = (south + north) / 2.0
    return (
        longitude_center - longitude_span / 2.0,
        max(-85.0, latitude_center - latitude_span / 2.0),
        longitude_center + longitude_span / 2.0,
        min(85.0, latitude_center + latitude_span / 2.0),
    )


def _walk_coordinates(node: Any) -> Iterable[tuple[float, float]]:
    """Yield every (lon, lat) pair inside an arbitrarily nested coordinate list."""
    if isinstance(node, (list, tuple)):
        if (
            len(node) >= 2
            and isinstance(node[0], (int, float))
            and isinstance(node[1], (int, float))
        ):
            yield float(node[0]), float(node[1])
        else:
            for child in node:
                yield from _walk_coordinates(child)


def _region_bounds(
    province_geojson: dict[str, Any] | None,
    district_geojson: dict[str, Any] | None,
    province_names: Collection[str] | None,
    district_names: Collection[str] | None,
    padding_percent: int,
) -> tuple[float, float, float, float] | None:
    """Bounds around the selected kabupaten/kota or provinces.

    Used to frame a publication map by administrative area when there are no
    earthquake points to fit the view to. Districts win over provinces so a
    kabupaten/kota selection zooms tighter than its parent province.
    """
    features: list[dict[str, Any]] = []
    if district_names and district_geojson:
        selected = set(district_names)
        features = [
            feature
            for feature in district_geojson.get("features", [])
            if (feature.get("properties") or {}).get("WADMKK") in selected
        ]
    elif province_names and province_geojson:
        selected = set(province_names)
        features = [
            feature
            for feature in province_geojson.get("features", [])
            if (feature.get("properties") or {}).get("PROVINSI") in selected
        ]
    longitudes: list[float] = []
    latitudes: list[float] = []
    for feature in features:
        geometry = feature.get("geometry") or {}
        for longitude, latitude in _walk_coordinates(geometry.get("coordinates")):
            longitudes.append(longitude)
            latitudes.append(latitude)
    if not longitudes or not latitudes:
        return None
    west, east = min(longitudes), max(longitudes)
    south, north = min(latitudes), max(latitudes)
    longitude_span = max(0.35, east - west)
    latitude_span = max(0.28, north - south)
    longitude_center = (west + east) / 2.0
    latitude_center = (south + north) / 2.0
    padding_factor = 1.0 + (padding_percent / 100.0) * 2.0
    longitude_span *= padding_factor
    latitude_span *= padding_factor
    return (
        longitude_center - longitude_span / 2.0,
        latitude_center - latitude_span / 2.0,
        longitude_center + longitude_span / 2.0,
        latitude_center + latitude_span / 2.0,
    )


def _choose_zoom(
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    max_tile_count: int = MAX_PUBLICATION_TILE_COUNT,
) -> int:
    west, south, east, north = bounds
    for zoom in range(13, 2, -1):
        left, top = _mercator_pixel(west, north, zoom)
        right, bottom = _mercator_pixel(east, south, zoom)
        tile_left = int(math.floor(left / 256))
        tile_right = int(math.floor((right - 1e-9) / 256))
        tile_top = int(math.floor(top / 256))
        tile_bottom = int(math.floor((bottom - 1e-9) / 256))
        tile_count = (tile_right - tile_left + 1) * (tile_bottom - tile_top + 1)
        if (
            right - left <= width * PUBLICATION_TILE_OVERSAMPLE_FACTOR
            and bottom - top <= height * PUBLICATION_TILE_OVERSAMPLE_FACTOR
            and tile_count <= max_tile_count
        ):
            return zoom
    return 3


def _runtime_directory() -> Path:
    local_app_data = os.getenv("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home()
    return base / "DatabaseInteraktif" / "runtime"


def _write_snapshot_log(message: str) -> None:
    """Persist tile/snapshot diagnostics for a windowed Nuitka process."""
    try:
        log_path = _runtime_directory() / "snapshot.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def _basemap_cache_directory() -> Path:
    override = os.getenv("DATABASE_INTERAKTIF_BASEMAP_CACHE")
    if override:
        return Path(override)
    local_app_data = os.getenv("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home()
    return base / "DatabaseInteraktif" / "cache" / "basemap"


def _valid_tile_bytes(payload: bytes) -> bool:
    if not payload:
        return False
    try:
        with Image.open(BytesIO(payload)) as tile:
            tile_size = tile.size
            tile.verify()
    except (UnidentifiedImageError, OSError):
        return False
    # Some providers legitimately return a fully transparent 256x256 PNG for
    # ocean/no-data cells. Such a tile can be only ~100 bytes, so byte length
    # is not a valid corruption signal. Validate the decoded tile dimensions.
    return tile_size == (256, 256)


def _decode_raster_tile(payload: bytes) -> Image.Image:
    """Decode a tile and composite valid transparency onto the map background."""
    with Image.open(BytesIO(payload)) as source:
        source.load()
        has_transparency = source.mode in {"RGBA", "LA"} or "transparency" in source.info
        if not has_transparency:
            return source.convert("RGB")
        rgba = source.convert("RGBA")
    background = Image.new("RGB", rgba.size, "#dfe8ec")
    background.paste(rgba, mask=rgba.getchannel("A"))
    return background


def _valid_vector_tile_bytes(payload: bytes) -> bool:
    if not payload:
        return False
    try:
        mapbox_vector_tile.decode(
            payload,
            default_options={"y_coord_down": True},
        )
    except Exception:
        return False
    return True


def _read_cached_tile(
    cache_path: Path,
    validator: Callable[[bytes], bool],
) -> bytes:
    try:
        payload = cache_path.read_bytes()
    except OSError:
        return b""
    if validator(payload):
        return payload
    try:
        cache_path.unlink()
    except OSError:
        pass
    return b""


def _write_cached_tile(
    cache_path: Path,
    payload: bytes,
    validator: Callable[[bytes], bool],
    *,
    label: str,
) -> bytes:
    """Atomically persist and re-read a tile before reporting it complete."""
    if not validator(payload):
        raise PublicationMapError(f"Tile {label} yang diunduh tidak valid.")

    temporary_path = cache_path.with_name(
        f"{cache_path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_bytes(payload)
        temporary_path.replace(cache_path)
        persisted = cache_path.read_bytes()
    except OSError as exc:
        _write_snapshot_log(
            f"cache-persist-failed label={label!r} path={str(cache_path)!r} "
            f"error={exc!r}"
        )
        try:
            temporary_path.unlink()
        except OSError:
            pass
        raise PublicationMapError(
            f"Tile {label} berhasil diunduh tetapi gagal disimpan ke cache."
        ) from exc

    if not validator(persisted):
        try:
            cache_path.unlink()
        except OSError:
            pass
        raise PublicationMapError(
            f"Tile {label} gagal diverifikasi setelah disimpan ke cache."
        )
    return persisted


def _url_cache_path(url: str, suffix: str) -> Path:
    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _basemap_cache_directory() / f"{cache_key}.{suffix}"


def _logical_raster_cache_path(
    templates: tuple[str, ...],
    *,
    zoom: int,
    tile_x: int,
    tile_y: int,
) -> Path:
    provider_identity = "\n".join(templates)
    logical_identity = (
        f"raster-v1\n{provider_identity}\n{zoom}\n{tile_x}\n{tile_y}"
    )
    cache_key = hashlib.sha256(logical_identity.encode("utf-8")).hexdigest()
    return _basemap_cache_directory() / f"{cache_key}.rtile"


_TILE_RETRY_NOT_BEFORE: dict[str, float] = {}
_TILE_RETRY_LOCK = threading.Lock()


def _retry_after_seconds(error: urllib.error.HTTPError) -> float:
    """Parse Retry-After as seconds or an HTTP date, with a short default."""
    raw_value = error.headers.get("Retry-After") if error.headers else None
    if raw_value:
        try:
            return max(0.0, float(raw_value.strip()))
        except (TypeError, ValueError):
            try:
                retry_at = parsedate_to_datetime(raw_value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return DEFAULT_RETRY_AFTER_SECONDS


def _tile_cooldown_remaining(cache_key: str) -> float:
    now = time.monotonic()
    with _TILE_RETRY_LOCK:
        retry_at = _TILE_RETRY_NOT_BEFORE.get(cache_key, 0.0)
        if retry_at <= now:
            _TILE_RETRY_NOT_BEFORE.pop(cache_key, None)
            return 0.0
        return retry_at - now


def _set_tile_cooldown(cache_key: str, seconds: float) -> None:
    retry_at = time.monotonic() + max(0.0, seconds)
    with _TILE_RETRY_LOCK:
        _TILE_RETRY_NOT_BEFORE[cache_key] = max(
            retry_at,
            _TILE_RETRY_NOT_BEFORE.get(cache_key, 0.0),
        )


def _clear_tile_cooldown(cache_key: str) -> None:
    with _TILE_RETRY_LOCK:
        _TILE_RETRY_NOT_BEFORE.pop(cache_key, None)


@lru_cache(maxsize=512)
def _download_raster_tile(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, TILE_DOWNLOAD_ATTEMPTS + 1):
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Database-Interaktif/1.0 (publication map)",
                "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=TILE_DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                payload = response.read(MAX_RASTER_TILE_BYTES + 1)
            if len(payload) > MAX_RASTER_TILE_BYTES:
                raise PublicationMapError("Respons tile raster terlalu besar.")
            if not _valid_tile_bytes(payload):
                raise PublicationMapError("Respons tile bukan gambar raster yang valid.")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 503}:
                retry_after = _retry_after_seconds(exc)
                _write_snapshot_log(
                    f"tile-rate-limited status={exc.code} retry_after={retry_after:.2f} "
                    f"url={url!r}"
                )
                raise TileRetryAfterError(
                    f"Server tile meminta tunggu {retry_after:.1f} detik.",
                    retry_after,
                ) from exc
            last_error = exc
            _write_snapshot_log(
                f"tile-http-failed status={exc.code} url={url!r} error={exc!r}"
            )
        except (urllib.error.URLError, TimeoutError, OSError, PublicationMapError) as exc:
            last_error = exc
            _write_snapshot_log(
                f"tile-attempt-failed attempt={attempt}/{TILE_DOWNLOAD_ATTEMPTS} "
                f"url={url!r} error={exc!r}"
            )
            if attempt < TILE_DOWNLOAD_ATTEMPTS:
                time.sleep(0.35 * (2 ** (attempt - 1)))
    raise PublicationMapError("Tile basemap tidak dapat diunduh.") from last_error


@lru_cache(maxsize=512)
def _download_vector_tile(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "DatabaseInteraktif/1.0 (publication map renderer)",
            "Accept": "application/vnd.mapbox-vector-tile,application/x-protobuf,*/*",
        },
    )
    last_error: Exception | None = None
    for attempt in range(1, TILE_DOWNLOAD_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(
                request,
                timeout=TILE_DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                payload = response.read(MAX_VECTOR_TILE_BYTES + 1)
            if len(payload) > MAX_VECTOR_TILE_BYTES:
                raise PublicationMapError("Respons tile vektor terlalu besar.")
            mapbox_vector_tile.decode(
                payload,
                default_options={"y_coord_down": True},
            )
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code in {429, 503}:
                retry_after = _retry_after_seconds(exc)
                _write_snapshot_log(
                    f"vector-tile-rate-limited status={exc.code} "
                    f"retry_after={retry_after:.2f} url={url!r}"
                )
                raise TileRetryAfterError(
                    f"Server tile vektor meminta tunggu {retry_after:.1f} detik.",
                    retry_after,
                ) from exc
            last_error = exc
            _write_snapshot_log(
                f"vector-tile-http-failed status={exc.code} "
                f"url={url!r} error={exc!r}"
            )
        except Exception as exc:
            last_error = exc
            _write_snapshot_log(
                f"vector-tile-attempt-failed "
                f"attempt={attempt}/{TILE_DOWNLOAD_ATTEMPTS} "
                f"url={url!r} error={exc!r}"
            )
            if attempt < TILE_DOWNLOAD_ATTEMPTS:
                time.sleep(0.35 * (2 ** (attempt - 1)))
    raise PublicationMapError("Tile vektor Shortbread tidak dapat diunduh.") from last_error


def _fetch_tile(url: str) -> bytes:
    """Return a raster tile only after its URL cache is verified on disk."""
    cache_path = _url_cache_path(url, "tile")
    cached = _read_cached_tile(cache_path, _valid_tile_bytes)
    if cached:
        return cached
    payload = _download_raster_tile(url)
    return _write_cached_tile(
        cache_path,
        payload,
        _valid_tile_bytes,
        label="raster",
    )


def _fetch_vector_tile(url: str) -> bytes:
    """Return a Shortbread tile only after its MVT cache is verified on disk."""
    cache_path = _url_cache_path(url, "mvt")
    cached = _read_cached_tile(cache_path, _valid_vector_tile_bytes)
    if cached:
        return cached
    cooldown_key = str(cache_path)
    cooldown = _tile_cooldown_remaining(cooldown_key)
    if cooldown > 0:
        raise TileRetryAfterError(
            f"Tile vektor masih menunggu {cooldown:.1f} detik.", cooldown
        )
    try:
        payload = _download_vector_tile(url)
    except TileRetryAfterError as exc:
        _set_tile_cooldown(cooldown_key, exc.retry_after_seconds)
        raise
    persisted = _write_cached_tile(
        cache_path,
        payload,
        _valid_vector_tile_bytes,
        label="vektor Shortbread",
    )
    _clear_tile_cooldown(cooldown_key)
    return persisted


def _basemap_tile_templates(basemap_name: str) -> tuple[str, ...]:
    if basemap_name == BASEMAP_RELIEF:
        return (
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
            "https://services.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
        )
    if basemap_name == BASEMAP_CYCLOSM:
        return tuple(
            f"https://{subdomain}.tile-cyclosm.openstreetmap.fr/cyclosm/{{z}}/{{x}}/{{y}}.png"
            for subdomain in ("a", "b", "c")
        )
    if basemap_name == BASEMAP_HUMANITARIAN:
        return tuple(
            f"https://{subdomain}.tile.openstreetmap.fr/hot/{{z}}/{{x}}/{{y}}.png"
            for subdomain in ("a", "b", "c")
        )
    return tuple(
        f"https://{subdomain}.tile.opentopomap.org/{{z}}/{{x}}/{{y}}.png"
        for subdomain in ("a", "b", "c")
    )


def _fetch_tile_from_templates(
    templates: tuple[str, ...],
    *,
    zoom: int,
    tile_x: int,
    tile_y: int,
) -> bytes:
    logical_cache_path = _logical_raster_cache_path(
        templates,
        zoom=zoom,
        tile_x=tile_x,
        tile_y=tile_y,
    )
    cached = _read_cached_tile(logical_cache_path, _valid_tile_bytes)
    if cached:
        return cached
    if not templates:
        raise PublicationMapError("Provider basemap tidak memiliki endpoint tile.")

    cooldown_key = str(logical_cache_path)
    cooldown = _tile_cooldown_remaining(cooldown_key)
    if cooldown > 0:
        raise TileRetryAfterError(
            f"Tile masih menunggu {cooldown:.1f} detik.", cooldown
        )

    # a/b/c adalah satu backend. Sebarkan tile secara deterministik, tetapi
    # lakukan tepat satu request per tile per ronde.
    template_index = (tile_x + tile_y * 3 + zoom) % len(templates)
    url = templates[template_index].format(z=zoom, x=tile_x, y=tile_y)
    try:
        payload = _fetch_tile(url)
    except TileRetryAfterError as exc:
        _set_tile_cooldown(cooldown_key, exc.retry_after_seconds)
        raise
    persisted = _write_cached_tile(
        logical_cache_path,
        payload,
        _valid_tile_bytes,
        label=f"raster z{zoom}/{tile_x}/{tile_y}",
    )
    _clear_tile_cooldown(cooldown_key)
    return persisted


def _render_raster_basemap(
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
    basemap_name: str,
    *,
    progress_callback: BasemapProgressCallback | None = None,
    max_tile_count: int = MAX_PUBLICATION_TILE_COUNT,
) -> tuple[Image.Image, str, int]:
    """Render every required tile from one raster provider."""
    width, height = size
    zoom = _choose_zoom(bounds, width, height, max_tile_count)
    west, south, east, north = bounds
    left, top = _mercator_pixel(west, north, zoom)
    right, bottom = _mercator_pixel(east, south, zoom)
    tile_left = int(math.floor(left / 256))
    tile_right = int(math.floor((right - 1e-9) / 256))
    tile_top = int(math.floor(top / 256))
    tile_bottom = int(math.floor((bottom - 1e-9) / 256))
    tile_count = (tile_right - tile_left + 1) * (tile_bottom - tile_top + 1)
    if tile_count > max_tile_count:
        raise PublicationMapError("Cakupan peta terlalu luas untuk dirender.")

    mosaic = Image.new(
        "RGB",
        ((tile_right - tile_left + 1) * 256, (tile_bottom - tile_top + 1) * 256),
        "#dfe8ec",
    )
    templates = _basemap_tile_templates(basemap_name)
    attribution = get_basemap_attribution(basemap_name, html=False)

    tile_coordinates = [
        (tile_x, tile_y)
        for tile_x in range(tile_left, tile_right + 1)
        for tile_y in range(tile_top, tile_bottom + 1)
    ]
    rendered_tiles: dict[tuple[int, int], Image.Image] = {}
    pending_coordinates: list[tuple[int, int]] = []
    for tile_x, tile_y in tile_coordinates:
        cache_path = _logical_raster_cache_path(
            templates,
            zoom=zoom,
            tile_x=tile_x,
            tile_y=tile_y,
        )
        cached = _read_cached_tile(cache_path, _valid_tile_bytes)
        if not cached:
            pending_coordinates.append((tile_x, tile_y))
            continue
        try:
            rendered_tiles[(tile_x, tile_y)] = _decode_raster_tile(cached)
        except (UnidentifiedImageError, OSError):
            try:
                cache_path.unlink()
            except OSError:
                pass
            pending_coordinates.append((tile_x, tile_y))

    if progress_callback is not None:
        cached_count = len(rendered_tiles)
        progress_callback(
            f"Cache {basemap_name}: {cached_count}/{tile_count} tile tersimpan; "
            f"mengunduh {len(pending_coordinates)} tile yang belum ada",
            cached_count,
            tile_count,
        )
    tile_errors: list[Exception] = []
    if pending_coordinates:
        with ThreadPoolExecutor(
            max_workers=min(TILE_DOWNLOAD_WORKERS, len(pending_coordinates))
        ) as executor:
            future_coordinates = {
                executor.submit(
                    _fetch_tile_from_templates,
                    templates,
                    zoom=zoom,
                    tile_x=tile_x,
                    tile_y=tile_y,
                ): (tile_x, tile_y)
                for tile_x, tile_y in pending_coordinates
            }
            for future in as_completed(future_coordinates):
                tile_x, tile_y = future_coordinates[future]
                try:
                    payload = future.result()
                    tile = _decode_raster_tile(payload)
                except Exception as exc:
                    tile_errors.append(exc)
                    continue
                rendered_tiles[(tile_x, tile_y)] = tile
                if progress_callback is not None:
                    completed = len(rendered_tiles)
                    progress_callback(
                        f"Mengunduh {basemap_name}: "
                        f"{completed}/{tile_count} tile tersimpan",
                        completed,
                        tile_count,
                    )

    if tile_errors:
        missing_tiles = tile_count - len(rendered_tiles)
        if progress_callback is not None:
            progress_callback(
                f"{basemap_name}: {missing_tiles} tile belum lengkap dan akan diulang",
                len(rendered_tiles),
                tile_count,
            )
        raise IncompleteBasemapError(
            f"{missing_tiles} dari {tile_count} tile {basemap_name} belum lengkap.",
            completed=len(rendered_tiles),
            total=tile_count,
        ) from tile_errors[-1]

    for (tile_x, tile_y), tile in rendered_tiles.items():
        mosaic.paste(tile, ((tile_x - tile_left) * 256, (tile_y - tile_top) * 256))

    crop_box = (
        int(round(left - tile_left * 256)),
        int(round(top - tile_top * 256)),
        int(round(right - tile_left * 256)),
        int(round(bottom - tile_top * 256)),
    )
    cropped = mosaic.crop(crop_box)
    return cropped.resize(size, Image.Resampling.LANCZOS), attribution, zoom


def _mvt_point(point: Collection[float], extent: float) -> tuple[float, float]:
    return float(point[0]) * 256.0 / extent, float(point[1]) * 256.0 / extent


def _mvt_lines(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "LineString":
        return [coordinates]
    if geometry_type == "MultiLineString":
        return coordinates
    return []


def _mvt_polygons(geometry: dict[str, Any]) -> list[list[list[list[float]]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        return [coordinates]
    if geometry_type == "MultiPolygon":
        return coordinates
    return []


def _draw_mvt_line(
    draw: ImageDraw.ImageDraw,
    geometry: dict[str, Any],
    extent: float,
    *,
    fill: str,
    width: int,
) -> None:
    for line in _mvt_lines(geometry):
        points = [_mvt_point(point, extent) for point in line]
        if len(points) >= 2:
            draw.line(points, fill=fill, width=max(1, width), joint="curve")


def _draw_mvt_polygon(
    tile: Image.Image,
    geometry: dict[str, Any],
    extent: float,
    *,
    fill: str,
    outline: str | None = None,
    width: int = 1,
) -> None:
    tile_draw = ImageDraw.Draw(tile)
    for polygon in _mvt_polygons(geometry):
        if not polygon:
            continue
        mask = Image.new("L", tile.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        exterior = [_mvt_point(point, extent) for point in polygon[0]]
        if len(exterior) < 3:
            continue
        mask_draw.polygon(exterior, fill=255)
        for hole in polygon[1:]:
            hole_points = [_mvt_point(point, extent) for point in hole]
            if len(hole_points) >= 3:
                mask_draw.polygon(hole_points, fill=0)
        tile.paste(ImageColor.getrgb(fill), (0, 0, 256, 256), mask)
        if outline:
            for ring in polygon:
                ring_points = [_mvt_point(point, extent) for point in ring]
                if len(ring_points) >= 2:
                    tile_draw.line(
                        ring_points + [ring_points[0]],
                        fill=outline,
                        width=max(1, width),
                        joint="curve",
                    )


def _shortbread_features(
    layers: dict[str, Any],
    layer_name: str,
) -> tuple[list[dict[str, Any]], float]:
    layer = layers.get(layer_name) or {}
    return list(layer.get("features") or []), float(layer.get("extent") or 4096)


def _render_shortbread_tile(
    payload: bytes,
    *,
    zoom: int,
    show_roads: bool,
    show_rivers: bool,
    topographic: bool,
) -> Image.Image:
    """Render the selected VersaTiles Shortbread data without another basemap."""
    try:
        layers = mapbox_vector_tile.decode(
            payload,
            default_options={"y_coord_down": True},
        )
    except Exception as exc:
        raise PublicationMapError("Tile vektor Shortbread tidak valid.") from exc

    land_background = "#e6dec6" if topographic else "#e8ecd8"
    tile = Image.new("RGB", (256, 256), land_background)

    def draw_polygons(
        layer_name: str,
        color: str,
        *,
        outline: str | None = None,
        width: int = 1,
    ) -> None:
        features, extent = _shortbread_features(layers, layer_name)
        for feature in features:
            _draw_mvt_polygon(
                tile,
                feature.get("geometry") or {},
                extent,
                fill=color,
                outline=outline,
                width=width,
            )

    def draw_lines(
        layer_name: str,
        color: str,
        *,
        width: int = 1,
        feature_filter: Any = None,
    ) -> None:
        features, extent = _shortbread_features(layers, layer_name)
        target_draw = ImageDraw.Draw(tile)
        for feature in features:
            properties = feature.get("properties") or {}
            if feature_filter is not None and not feature_filter(properties):
                continue
            _draw_mvt_line(
                target_draw,
                feature.get("geometry") or {},
                extent,
                fill=color,
                width=width,
            )

    draw_polygons("ocean", "#8fc6dd", outline="#66a9c5")

    land_colors = {
        "forest": "#a7c689" if topographic else "#b7d39f",
        "wood": "#a7c689" if topographic else "#b7d39f",
        "grass": "#c4d69d" if topographic else "#d0e1b6",
        "farmland": "#ddd0a0" if topographic else "#e8dfa9",
        "farmyard": "#d3c79a" if topographic else "#dfd4a5",
        "residential": "#e9e6dc",
        "industrial": "#d8d5c9",
        "cemetery": "#c9dcb7",
        "sand": "#eee0b6",
        "beach": "#f2dfa8",
    }
    land_features, land_extent = _shortbread_features(layers, "land")
    for feature in land_features:
        kind = str((feature.get("properties") or {}).get("kind") or "")
        _draw_mvt_polygon(
            tile,
            feature.get("geometry") or {},
            land_extent,
            fill=land_colors.get(kind, land_background),
        )

    if zoom >= 11:
        draw_polygons("sites", "#e4dfd3")

    water_features, water_extent = _shortbread_features(layers, "water_polygons")
    for feature in water_features:
        kind = str((feature.get("properties") or {}).get("kind") or "")
        if show_rivers or kind != "river":
            _draw_mvt_polygon(
                tile,
                feature.get("geometry") or {},
                water_extent,
                fill="#7ebbd6",
                outline="#579ab8",
            )
    if show_rivers:
        draw_lines("water_lines", "#327fa3", width=1)

    draw_polygons("dam_polygons", "#aeb8b9", outline="#7d8b91")
    draw_lines("dam_lines", "#7d8b91", width=1)

    if show_roads:
        draw_polygons("street_polygons", "#f8f5ef", outline="#d7d1c9")
        street_features, street_extent = _shortbread_features(layers, "streets")
        street_draw = ImageDraw.Draw(tile)
        for feature in street_features:
            kind = str((feature.get("properties") or {}).get("kind") or "")
            major = kind in {"motorway", "trunk", "primary", "secondary"}
            _draw_mvt_line(
                street_draw,
                feature.get("geometry") or {},
                street_extent,
                fill="#d99b72" if major else "#ffffff",
                width=2 if major else 1,
            )
        draw_polygons("bridges", "#f5f1e8", outline="#a89f95")

    if zoom >= 13:
        draw_polygons("buildings", "#d4cbc0", outline="#b8aca0")

    boundary_features, boundary_extent = _shortbread_features(layers, "boundaries")
    boundary_draw = ImageDraw.Draw(tile)
    for feature in boundary_features:
        properties = feature.get("properties") or {}
        try:
            national = int(properties.get("admin_level") or 9) <= 2
        except (TypeError, ValueError):
            national = False
        _draw_mvt_line(
            boundary_draw,
            feature.get("geometry") or {},
            boundary_extent,
            fill="#4e5965" if national else "#7b8792",
            width=2 if national else 1,
        )

    draw_lines("ferries", "#4d9cb8", width=1)
    draw_lines("aerialways", "#8a7f78", width=1)
    draw_lines("pier_lines", "#87929b", width=1)
    draw_polygons("pier_polygons", "#d9d6ce", outline="#87929b")
    return tile


def _render_shortbread_basemap(
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
    *,
    show_roads: bool,
    show_rivers: bool,
    topographic: bool,
    progress_callback: BasemapProgressCallback | None = None,
    max_tile_count: int = MAX_PUBLICATION_TILE_COUNT,
) -> tuple[Image.Image, str, int]:
    width, height = size
    zoom = min(14, _choose_zoom(bounds, width, height, max_tile_count))
    west, south, east, north = bounds
    left, top = _mercator_pixel(west, north, zoom)
    right, bottom = _mercator_pixel(east, south, zoom)
    tile_left = int(math.floor(left / 256))
    tile_right = int(math.floor((right - 1e-9) / 256))
    tile_top = int(math.floor(top / 256))
    tile_bottom = int(math.floor((bottom - 1e-9) / 256))
    tile_count = (tile_right - tile_left + 1) * (tile_bottom - tile_top + 1)
    if tile_count > max_tile_count:
        raise PublicationMapError("Cakupan Shortbread terlalu luas untuk dirender.")

    tile_coordinates = [
        (tile_x, tile_y)
        for tile_x in range(tile_left, tile_right + 1)
        for tile_y in range(tile_top, tile_bottom + 1)
    ]
    rendered_tiles: dict[tuple[int, int], Image.Image] = {}
    pending_coordinates: list[tuple[int, int]] = []
    for tile_x, tile_y in tile_coordinates:
        url = f"https://tiles.versatiles.org/tiles/osm/{zoom}/{tile_x}/{tile_y}"
        cache_path = _url_cache_path(url, "mvt")
        cached = _read_cached_tile(cache_path, _valid_vector_tile_bytes)
        if not cached:
            pending_coordinates.append((tile_x, tile_y))
            continue
        try:
            rendered_tiles[(tile_x, tile_y)] = _render_shortbread_tile(
                cached,
                zoom=zoom,
                show_roads=show_roads,
                show_rivers=show_rivers,
                topographic=topographic,
            )
        except Exception:
            try:
                cache_path.unlink()
            except OSError:
                pass
            pending_coordinates.append((tile_x, tile_y))

    if progress_callback is not None:
        cached_count = len(rendered_tiles)
        progress_callback(
            f"Cache {BASEMAP_SHORTBREAD}: {cached_count}/{tile_count} tile tersimpan; "
            f"mengunduh {len(pending_coordinates)} tile yang belum ada",
            cached_count,
            tile_count,
        )
    tile_errors: list[Exception] = []
    if pending_coordinates:
        with ThreadPoolExecutor(
            max_workers=min(TILE_DOWNLOAD_WORKERS, len(pending_coordinates))
        ) as executor:
            future_coordinates = {
                executor.submit(
                    _fetch_vector_tile,
                    f"https://tiles.versatiles.org/tiles/osm/{zoom}/{tile_x}/{tile_y}",
                ): (tile_x, tile_y)
                for tile_x, tile_y in pending_coordinates
            }
            for future in as_completed(future_coordinates):
                tile_x, tile_y = future_coordinates[future]
                try:
                    rendered_tiles[(tile_x, tile_y)] = _render_shortbread_tile(
                        future.result(),
                        zoom=zoom,
                        show_roads=show_roads,
                        show_rivers=show_rivers,
                        topographic=topographic,
                    )
                except Exception as exc:
                    tile_errors.append(exc)
                    continue
                if progress_callback is not None:
                    completed = len(rendered_tiles)
                    progress_callback(
                        f"Mengunduh {BASEMAP_SHORTBREAD}: "
                        f"{completed}/{tile_count} tile tersimpan",
                        completed,
                        tile_count,
                    )

    if tile_errors:
        missing_tiles = tile_count - len(rendered_tiles)
        if progress_callback is not None:
            progress_callback(
                f"{BASEMAP_SHORTBREAD}: {missing_tiles} tile belum lengkap dan akan diulang",
                len(rendered_tiles),
                tile_count,
            )
        raise IncompleteBasemapError(
            f"{missing_tiles} dari {tile_count} tile {BASEMAP_SHORTBREAD} belum lengkap.",
            completed=len(rendered_tiles),
            total=tile_count,
        ) from tile_errors[-1]

    mosaic = Image.new(
        "RGB",
        ((tile_right - tile_left + 1) * 256, (tile_bottom - tile_top + 1) * 256),
        "#dfe8ec",
    )
    for (tile_x, tile_y), tile in rendered_tiles.items():
        mosaic.paste(tile, ((tile_x - tile_left) * 256, (tile_y - tile_top) * 256))

    crop_box = (
        int(round(left - tile_left * 256)),
        int(round(top - tile_top * 256)),
        int(round(right - tile_left * 256)),
        int(round(bottom - tile_top * 256)),
    )
    cropped = mosaic.crop(crop_box).resize(size, Image.Resampling.LANCZOS)
    return cropped, get_basemap_attribution(BASEMAP_SHORTBREAD, html=False), zoom


def _render_basemap(
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
    basemap_name: str,
    *,
    show_shortbread_roads: bool = False,
    show_shortbread_rivers: bool = True,
    show_shortbread_topography: bool = False,
    progress_callback: BasemapProgressCallback | None = None,
    retry_timeout_seconds: float = BASEMAP_COMPLETION_TIMEOUT_SECONDS,
    max_retry_rounds: int = BASEMAP_COMPLETION_MAX_ROUNDS,
    max_tile_count: int = MAX_PUBLICATION_TILE_COUNT,
) -> tuple[Image.Image, str, int]:
    """Wait for one selected provider to produce a complete basemap.

    Successful tile downloads are cached by the fetchers.  Consequently each
    retry round reads those tiles locally and spends network time only on the
    missing subset.  A failed provider is never replaced and never converted
    into a neutral/empty publication image.
    """
    timeout_seconds = max(0.0, float(retry_timeout_seconds))
    retry_rounds = max(1, int(max_retry_rounds))
    deadline = time.monotonic() + timeout_seconds
    last_error: PublicationMapError | None = None
    last_completed = 0
    last_total = 0

    for round_number in range(1, retry_rounds + 1):
        try:
            if basemap_name == BASEMAP_SHORTBREAD:
                return _render_shortbread_basemap(
                    bounds,
                    size,
                    show_roads=show_shortbread_roads,
                    show_rivers=show_shortbread_rivers,
                    topographic=show_shortbread_topography,
                    progress_callback=progress_callback,
                    max_tile_count=max_tile_count,
                )
            return _render_raster_basemap(
                bounds,
                size,
                basemap_name,
                progress_callback=progress_callback,
                max_tile_count=max_tile_count,
            )
        except PublicationMapError as exc:
            last_error = exc
            if isinstance(exc, IncompleteBasemapError):
                last_completed = exc.completed
                last_total = exc.total
            remaining_seconds = deadline - time.monotonic()
            _write_snapshot_log(
                f"basemap-round-incomplete requested={basemap_name!r} "
                f"round={round_number}/{retry_rounds} error={exc!r}; "
                "same-provider-retry"
            )
            if round_number >= retry_rounds or remaining_seconds <= 0:
                break
            delay_seconds = min(BASEMAP_RETRY_DELAY_SECONDS, remaining_seconds)
            missing_tiles = max(1, last_total - last_completed) if last_total else 1
            estimated_next_round = (
                math.ceil(missing_tiles / TILE_DOWNLOAD_WORKERS)
                * TILE_DOWNLOAD_TIMEOUT_SECONDS
            )
            if remaining_seconds < delay_seconds + estimated_next_round:
                break
            if progress_callback is not None:
                progress = (
                    f"{last_completed}/{last_total} tile tersimpan. "
                    if last_total
                    else ""
                )
                progress_callback(
                    f"Tile {basemap_name} belum lengkap; {progress}Mencoba provider yang sama "
                    f"lagi dalam {delay_seconds:.0f} detik "
                    f"(putaran {round_number + 1}/{retry_rounds}).",
                    0,
                    0,
                )
            time.sleep(delay_seconds)

    _write_snapshot_log(
        f"basemap-retry-exhausted requested={basemap_name!r} "
        f"error={last_error!r}; no-output-created"
    )
    progress_summary = (
        f" {last_completed}/{last_total} tile berhasil tersimpan; "
        f"{last_total - last_completed} tile masih gagal."
        if last_total
        else ""
    )
    raise PublicationMapError(
        f"Basemap {basemap_name} belum dapat dimuat lengkap dalam anggaran "
        f"{timeout_seconds:.0f} detik.{progress_summary} Tidak ada file kosong, "
        "PNG/PDF parsial, atau basemap pengganti yang dibuat; silakan coba lagi "
        "saat layanan tersedia."
    ) from last_error


def _iter_polygon_rings(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon":
        yield from coordinates
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            yield from polygon


def _iter_lines(geometry: dict[str, Any]) -> Iterable[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "LineString":
        yield coordinates
    elif geometry_type == "MultiLineString":
        yield from coordinates


def _projector(
    bounds: tuple[float, float, float, float],
    zoom: int,
    box: tuple[int, int, int, int],
):
    west, south, east, north = bounds
    x0, y0 = _mercator_pixel(west, north, zoom)
    x1, y1 = _mercator_pixel(east, south, zoom)
    box_x, box_y, width, height = box

    def project(longitude: float, latitude: float) -> tuple[float, float]:
        world_x, world_y = _mercator_pixel(longitude, latitude, zoom)
        return (
            box_x + (world_x - x0) / (x1 - x0) * width,
            box_y + (world_y - y0) / (y1 - y0) * height,
        )

    return project


def _render_hazard_overlay(
    layer: PublicationHazardLayer,
    bounds: tuple[float, float, float, float],
    zoom: int,
    size: tuple[int, int],
    alpha: int = 200,
) -> Image.Image:
    """Sample the hazard grid at every map pixel centre (Web Mercator view).

    Sampling per output pixel keeps cell edges at their true longitude and
    latitude and crops the grid to the map frame without resampling blur.
    """
    width, height = size
    west, south, east, north = bounds
    x0, y0 = _mercator_pixel(west, north, zoom)
    x1, y1 = _mercator_pixel(east, south, zoom)
    world_size = 256.0 * (2**zoom)
    world_x = x0 + (np.arange(width) + 0.5) / width * (x1 - x0)
    world_y = y0 + (np.arange(height) + 0.5) / height * (y1 - y0)
    longitudes = world_x / world_size * 360.0 - 180.0
    latitudes = np.degrees(np.arctan(np.sinh(np.pi * (1.0 - 2.0 * world_y / world_size))))
    classes = np.asarray(layer.classes, dtype=int)
    row_count, column_count = classes.shape
    grid_west, grid_south, grid_east, grid_north = layer.bounds
    columns = np.floor(
        (longitudes - grid_west) / (grid_east - grid_west) * column_count
    ).astype(int)
    rows = np.floor((grid_north - latitudes) / (grid_north - grid_south) * row_count).astype(int)
    sampled = classes[np.clip(rows, 0, row_count - 1)][:, np.clip(columns, 0, column_count - 1)]
    sampled[(rows < 0) | (rows >= row_count), :] = -1
    sampled[:, (columns < 0) | (columns >= column_count)] = -1
    palette = np.asarray([ImageColor.getrgb(color) for color in layer.colors], dtype=np.uint8)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    valid = sampled >= 0
    rgba[valid, :3] = palette[sampled[valid]]
    rgba[valid, 3] = alpha
    return Image.fromarray(rgba)


def _visible_fault_features(
    fault_geojson: dict[str, Any],
    fault_names: Collection[str] | None,
    fault_feature_indexes: Collection[int] | None,
) -> list[dict[str, Any]]:
    """Faults that pass the same name/index filters used to draw them."""
    selected_faults = None if fault_names is None else set(fault_names)
    selected_indexes = (
        None if fault_feature_indexes is None else set(fault_feature_indexes)
    )
    features: list[dict[str, Any]] = []
    for feature_index, feature in enumerate(fault_geojson.get("features", [])):
        if selected_indexes is not None and feature_index not in selected_indexes:
            continue
        if (
            selected_faults is not None
            and feature.get("properties", {}).get("Name") not in selected_faults
        ):
            continue
        features.append(feature)
    return features


def _clip_segment_to_bounds(
    start: tuple[float, float],
    end: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Liang-Barsky clip of a lon/lat segment against the map rectangle."""
    west, south, east, north = bounds
    x0, y0 = start
    x1, y1 = end
    delta_x = x1 - x0
    delta_y = y1 - y0
    t_enter, t_exit = 0.0, 1.0
    for edge, offset in (
        (-delta_x, x0 - west),
        (delta_x, east - x0),
        (-delta_y, y0 - south),
        (delta_y, north - y0),
    ):
        if edge == 0:
            if offset < 0:
                return None  # Parallel to and outside this edge.
            continue
        ratio = offset / edge
        if edge < 0:
            if ratio > t_exit:
                return None
            t_enter = max(t_enter, ratio)
        else:
            if ratio < t_enter:
                return None
            t_exit = min(t_exit, ratio)
    if t_enter > t_exit:
        return None
    return (
        (x0 + t_enter * delta_x, y0 + t_enter * delta_y),
        (x0 + t_exit * delta_x, y0 + t_exit * delta_y),
    )


def _fault_label_anchors_in_view(
    fault_features: list[dict[str, Any]],
    bounds: tuple[float, float, float, float],
) -> list[tuple[str, float, float]]:
    """One label per fault name, anchored on the part inside the map view.

    ``get_fault_label_records`` anchors a label at the midpoint of a fault's
    whole geometry. When the map is filtered to a kabupaten/kota, a long fault
    that only crosses the area keeps its anchor outside the view and loses its
    name. Here each fault is anchored at the midpoint of its longest run that
    actually falls within ``bounds``, so every fault touching the view is named.
    """
    best_by_name: dict[str, tuple[float, tuple[float, float]]] = {}
    for feature in fault_features:
        name = str((feature.get("properties") or {}).get("Name") or "").strip()
        if not name:
            continue
        for line in _iter_lines(feature.get("geometry") or {}):
            for start, end in zip(line, line[1:]):
                clipped = _clip_segment_to_bounds(
                    (float(start[0]), float(start[1])),
                    (float(end[0]), float(end[1])),
                    bounds,
                )
                if clipped is None:
                    continue
                (cx0, cy0), (cx1, cy1) = clipped
                length = math.hypot(cx1 - cx0, cy1 - cy0)
                previous = best_by_name.get(name)
                if previous is None or length > previous[0]:
                    best_by_name[name] = (
                        length,
                        ((cx0 + cx1) / 2.0, (cy0 + cy1) / 2.0),
                    )
    return [
        (name, midpoint[1], midpoint[0])
        for name, (length, midpoint) in sorted(best_by_name.items())
    ]


def _draw_snapshot_fault_labels(
    canvas: Image.Image,
    bounds: tuple[float, float, float, float],
    map_box: tuple[int, int, int, int],
    scale: float,
    fault_geojson: dict[str, Any],
    fault_names: Collection[str] | None,
    fault_feature_indexes: Collection[int] | None,
) -> None:
    """Label every fault touching the view on top of a pasted map snapshot.

    Leaflet only reveals its fault labels from zoom 9 upward, so publication
    maps covering a province or wider lose them entirely. Drawing the labels on
    the canvas with the same Web Mercator projection the snapshot was fitted to
    keeps every fault name visible at any extent, and anchoring on the part
    inside the view means a kabupaten/kota filter still names the faults that
    only cross the selected area.
    """
    features = _visible_fault_features(
        fault_geojson, fault_names, fault_feature_indexes
    )
    if not features:
        return
    box_width, box_height = map_box[2], map_box[3]
    overlay = Image.new("RGBA", (box_width, box_height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay, "RGBA")
    project = _projector(bounds, 0, (0, 0, box_width, box_height))
    fault_label_font = _font(max(8, int(9 * scale)), bold=True)
    for name, latitude, longitude in _fault_label_anchors_in_view(features, bounds):
        label_x, label_y = project(longitude, latitude)
        if not (0 <= label_x <= box_width and 0 <= label_y <= box_height):
            continue
        overlay_draw.text(
            (label_x, label_y),
            name,
            font=fault_label_font,
            fill="#000000",
            anchor="mm",
            stroke_width=max(1, int(2 * scale)),
            stroke_fill="#ffffff",
        )
    canvas.paste(overlay, (map_box[0], map_box[1]), overlay)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fill: str,
    width: int,
    dash: int,
) -> None:
    for start, end in zip(points, points[1:]):
        delta_x = end[0] - start[0]
        delta_y = end[1] - start[1]
        length = math.hypot(delta_x, delta_y)
        if length == 0:
            continue
        position = 0.0
        while position < length:
            segment_end = min(position + dash, length)
            draw.line(
                [
                    (
                        start[0] + delta_x * position / length,
                        start[1] + delta_y * position / length,
                    ),
                    (
                        start[0] + delta_x * segment_end / length,
                        start[1] + delta_y * segment_end / length,
                    ),
                ],
                fill=fill,
                width=width,
            )
            position += dash * 2


def _draw_tectonic_symbol(
    draw: ImageDraw.ImageDraw,
    origin: tuple[float, float],
    width: float,
    symbol_type: str,
    scale: float,
) -> None:
    x, y = origin
    color = "#1f2937" if symbol_type == "Tunjaman" else "#c2185b"
    line_width = max(2, int(2.2 * scale))
    if symbol_type == "Sesar Geser":
        line_y = y + 8 * scale
        upper_y = line_y - 4 * scale
        lower_y = line_y + 4 * scale
        draw.line((x, line_y, x + width, line_y), fill=color, width=line_width)
        arrow = 5 * scale
        draw.line((x + width * 0.8, upper_y, x + width * 0.3, upper_y), fill=color, width=line_width)
        draw.line(
            (x + width * 0.3, upper_y, x + width * 0.3 + arrow, upper_y - arrow),
            fill=color,
            width=line_width,
        )
        draw.line(
            (x + width * 0.3, upper_y, x + width * 0.3 + arrow, upper_y + arrow),
            fill=color,
            width=line_width,
        )
        draw.line((x + width * 0.2, lower_y, x + width * 0.7, lower_y), fill=color, width=line_width)
        draw.line(
            (x + width * 0.7, lower_y, x + width * 0.7 - arrow, lower_y - arrow),
            fill=color,
            width=line_width,
        )
        draw.line(
            (x + width * 0.7, lower_y, x + width * 0.7 - arrow, lower_y + arrow),
            fill=color,
            width=line_width,
        )
        return

    line_y = y + 6 * scale
    draw.line((x, line_y, x + width, line_y), fill=color, width=line_width)

    positions = (0.28, 0.58, 0.86)
    if symbol_type == "Tunjaman":
        size = 6.5 * scale
        base_y = line_y - 2.5 * scale
        for fraction in positions:
            center_x = x + width * fraction
            draw.polygon(
                (
                    (center_x - size, base_y),
                    (center_x + size, base_y),
                    (center_x, base_y - size * 1.35),
                ),
                fill=color,
            )
    elif symbol_type == "Sesar Naik":
        triangle_half_width = 5 * scale
        triangle_height = 7 * scale
        for fraction in positions:
            center_x = x + width * fraction
            draw.polygon(
                (
                    (center_x - triangle_half_width, line_y + scale),
                    (center_x + triangle_half_width, line_y + scale),
                    (center_x, line_y + scale + triangle_height),
                ),
                fill=color,
            )
    elif symbol_type == "Sesar Normal":
        square_size = 7 * scale
        for fraction in positions:
            center_x = x + width * fraction
            center_y = line_y + 5 * scale
            draw.rectangle(
                (
                    center_x - square_size / 2,
                    center_y,
                    center_x + square_size / 2,
                    center_y + square_size,
                ),
                fill=color,
            )


def _polyline_symbol_positions(
    points: list[tuple[float, float]],
    lengths: list[float],
    symbol_count: int,
) -> list[tuple[float, float, float, float, float, float]]:
    total_length = sum(lengths)
    positions: list[tuple[float, float, float, float, float, float]] = []
    for symbol_index in range(symbol_count):
        target = total_length * (symbol_index + 1) / (symbol_count + 1)
        travelled = 0.0
        for start, end, segment_length in zip(points, points[1:], lengths):
            if segment_length <= 0:
                continue
            if travelled + segment_length < target:
                travelled += segment_length
                continue
            fraction = (target - travelled) / segment_length
            center_x = start[0] + (end[0] - start[0]) * fraction
            center_y = start[1] + (end[1] - start[1]) * fraction
            unit_x = (end[0] - start[0]) / segment_length
            unit_y = (end[1] - start[1]) / segment_length
            positions.append(
                (center_x, center_y, unit_x, unit_y, -unit_y, unit_x)
            )
            break
    return positions


def _draw_trench_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    scale: float,
) -> None:
    """Draw enlarged trench teeth fully offset to one side of the centerline."""
    if len(points) < 2:
        return
    color = "#1f2937"
    line_width = max(2, int(2.4 * scale))
    draw.line(points, fill=color, width=line_width, joint="curve")
    lengths = [
        math.hypot(end[0] - start[0], end[1] - start[1])
        for start, end in zip(points, points[1:])
    ]
    total_length = sum(lengths)
    if total_length <= 0:
        return
    symbol_count = min(
        28,
        max(3, math.ceil(total_length / max(1, 58 * scale))),
    )
    half_base = 6.5 * scale
    height = 10 * scale
    centerline_gap = 3 * scale
    for center_x, center_y, unit_x, unit_y, normal_x, normal_y in (
        _polyline_symbol_positions(points, lengths, symbol_count)
    ):
        # Pillow uses screen coordinates (positive Y points downward), while
        # Leaflet's PolyLineTextPath renders the ``▲`` teeth on the opposite
        # local side of the directed path.  Flip the normal here so exported
        # publication maps match the interactive trench orientation.
        normal_x = -normal_x
        normal_y = -normal_y
        base_x = center_x + normal_x * centerline_gap
        base_y = center_y + normal_y * centerline_gap
        draw.polygon(
            (
                (base_x - unit_x * half_base, base_y - unit_y * half_base),
                (base_x + unit_x * half_base, base_y + unit_y * half_base),
                (base_x + normal_x * height, base_y + normal_y * height),
            ),
            fill=color,
        )


def _draw_fault_polyline(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    fault_class: str,
    scale: float,
    type_code: str | None = None,
) -> None:
    if len(points) < 2:
        return
    color = "#c2185b"
    line_width = max(2, int(2.5 * scale))
    draw.line(points, fill=color, width=line_width, joint="curve")

    lengths = [
        math.hypot(end[0] - start[0], end[1] - start[1])
        for start, end in zip(points, points[1:])
    ]
    total_length = sum(lengths)
    if total_length <= 0:
        return
    symbol_count = (
        min(10, max(3, math.ceil(total_length / max(1, 80 * scale))))
        if fault_class in {"Sesar Naik", "Sesar Normal"}
        else 1
    )
    for center_x, center_y, unit_x, unit_y, normal_x, normal_y in (
        _polyline_symbol_positions(points, lengths, symbol_count)
    ):
        if fault_class == "Sesar Geser":
            sense = get_strike_slip_sense(type_code)
            arrow_length = 9 * scale
            arrow_offset = 4 * scale
            arrow_head = 3.5 * scale
            for side, direction in ((-1, sense), (1, -sense)):
                offset_x = normal_x * arrow_offset * side
                offset_y = normal_y * arrow_offset * side
                start_x = center_x + offset_x - unit_x * arrow_length * direction
                start_y = center_y + offset_y - unit_y * arrow_length * direction
                tip_x = center_x + offset_x + unit_x * arrow_length * direction
                tip_y = center_y + offset_y + unit_y * arrow_length * direction
                draw.line((start_x, start_y, tip_x, tip_y), fill=color, width=line_width)
                for head_side in (-1, 1):
                    head_x = tip_x - unit_x * arrow_head * direction + normal_x * arrow_head * head_side
                    head_y = tip_y - unit_y * arrow_head * direction + normal_y * arrow_head * head_side
                    draw.line((tip_x, tip_y, head_x, head_y), fill=color, width=line_width)
        elif fault_class == "Sesar Naik":
            side = get_fault_symbol_side(type_code, unit_x, unit_y)
            half_width = 5 * scale
            near = 2 * scale * side
            far = 9 * scale * side
            draw.polygon(
                [
                    (center_x - unit_x * half_width + normal_x * near, center_y - unit_y * half_width + normal_y * near),
                    (center_x + unit_x * half_width + normal_x * near, center_y + unit_y * half_width + normal_y * near),
                    (center_x + normal_x * far, center_y + normal_y * far),
                ],
                fill=color,
            )
        elif fault_class == "Sesar Normal":
            side = get_fault_symbol_side(type_code, unit_x, unit_y)
            square = 7 * scale
            center_square_x = center_x + normal_x * 6 * scale * side
            center_square_y = center_y + normal_y * 6 * scale * side
            draw.polygon(
                (
                    (center_square_x - unit_x * square / 2 - normal_x * square / 2, center_square_y - unit_y * square / 2 - normal_y * square / 2),
                    (center_square_x + unit_x * square / 2 - normal_x * square / 2, center_square_y + unit_y * square / 2 - normal_y * square / 2),
                    (center_square_x + unit_x * square / 2 + normal_x * square / 2, center_square_y + unit_y * square / 2 + normal_y * square / 2),
                    (center_square_x - unit_x * square / 2 + normal_x * square / 2, center_square_y - unit_y * square / 2 + normal_y * square / 2),
                ),
                fill=color,
            )


def _wrap_centered_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    width: int,
    font: ImageFont.ImageFont,
) -> list[str]:
    """Lines that _draw_centered_wrapped_text will render (capped at three)."""
    lines: list[str] = []
    for paragraph in text.strip().splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines[:3]


def _draw_centered_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    x, y, width, height = box
    lines = _wrap_centered_lines(draw, text, width, font)
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + max(4, height // 40)
    top = y + max(0, (height - len(lines) * line_height) // 2)
    for index, line in enumerate(lines[:3]):
        text_box = draw.textbbox((0, 0), line, font=font)
        draw.text(
            (x + (width - (text_box[2] - text_box[0])) / 2, top + index * line_height),
            line,
            font=font,
            fill=fill,
        )


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    font: ImageFont.ImageFont,
    fill: str,
) -> None:
    x, y, width, height = box
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=font)[2] <= width or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + 4
    for index, line in enumerate(lines):
        line_y = y + index * line_height
        if line_y + line_height > y + height:
            break
        draw.text((x, line_y), line, font=font, fill=fill)


def _format_coordinate_label(
    value: float,
    positive_hemisphere: str,
    negative_hemisphere: str,
) -> str:
    """Format a coordinate as an unambiguous degree-minute-second label."""
    hemisphere = positive_hemisphere if value >= 0 else negative_hemisphere
    absolute = abs(value)
    degrees = int(math.floor(absolute + 1e-10))
    minutes_float = (absolute - degrees) * 60.0
    minutes = int(math.floor(minutes_float + 1e-8))
    seconds = int(round((minutes_float - minutes) * 60.0))
    if seconds == 60:
        seconds = 0
        minutes += 1
    if minutes == 60:
        minutes = 0
        degrees += 1
    return f"{degrees}°{minutes:02d}′{seconds:02d}″{hemisphere}"


def _grid_values(start: float, end: float, step: float) -> list[float]:
    """Return stable interior graticule values without floating-point drift."""
    value = math.ceil((start - 1e-10) / step) * step
    values: list[float] = []
    while value <= end + 1e-9:
        if start + 1e-9 < value < end - 1e-9:
            values.append(value)
        value += step
    return values


def _draw_rotated_coordinate_label(
    canvas: Image.Image,
    label: str,
    center: tuple[float, float],
    font: ImageFont.ImageFont,
    angle: int,
) -> None:
    text_box = font.getbbox(label)
    label_image = Image.new(
        "RGBA",
        (text_box[2] - text_box[0] + 8, text_box[3] - text_box[1] + 8),
        (255, 255, 255, 0),
    )
    label_draw = ImageDraw.Draw(label_image)
    label_draw.text(
        (4 - text_box[0], 4 - text_box[1]),
        label,
        font=font,
        fill=COORDINATE_FRAME_COLOR,
    )
    label_image = label_image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    canvas.paste(
        label_image,
        (
            round(center[0] - label_image.width / 2),
            round(center[1] - label_image.height / 2),
        ),
        label_image,
    )


def _draw_coordinate_grid(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    box: tuple[int, int, int, int],
) -> None:
    """Draw a four-sided labelled graticule and alternating map-edge frame."""
    west, south, east, north = bounds
    x, y, width, height = box
    font = _font(max(14, width // 75))
    project = _projector(bounds, 0, (x, y, width, height))

    longitude_step = _nice_grid_step(east - west)
    longitudes = _grid_values(west, east, longitude_step)
    longitude_ticks: list[tuple[float, float]] = []
    for longitude in longitudes:
        tick_x, _ = project(longitude, (south + north) / 2.0)
        if x <= tick_x <= x + width:
            draw.line((tick_x, y, tick_x, y + height), fill="#ffffff70", width=1)
            longitude_ticks.append((longitude, tick_x))

    latitude_step = _nice_grid_step(north - south)
    latitudes = _grid_values(south, north, latitude_step)
    latitude_ticks: list[tuple[float, float]] = []
    for latitude in latitudes:
        _, tick_y = project((west + east) / 2.0, latitude)
        if y <= tick_y <= y + height:
            draw.line((x, tick_y, x + width, tick_y), fill="#ffffff70", width=1)
            latitude_ticks.append((latitude, tick_y))

    band = max(8, min(14, width // 125))
    longitude_boundaries = [float(x), *(tick_x for _, tick_x in longitude_ticks), float(x + width)]
    latitude_boundaries = [
        float(y),
        *(tick_y for _, tick_y in reversed(latitude_ticks)),
        float(y + height),
    ]

    for index, (left, right) in enumerate(zip(longitude_boundaries, longitude_boundaries[1:])):
        fill = COORDINATE_FRAME_COLOR if index % 2 == 0 else "#ffffff"
        draw.rectangle(
            (round(left), y - band, round(right), y),
            fill=fill,
            outline=COORDINATE_FRAME_COLOR,
            width=1,
        )
        draw.rectangle(
            (round(left), y + height, round(right), y + height + band),
            fill=fill,
            outline=COORDINATE_FRAME_COLOR,
            width=1,
        )

    for index, (top, bottom) in enumerate(zip(latitude_boundaries, latitude_boundaries[1:])):
        fill = COORDINATE_FRAME_COLOR if index % 2 == 0 else "#ffffff"
        draw.rectangle(
            (x - band, round(top), x, round(bottom)),
            fill=fill,
            outline=COORDINATE_FRAME_COLOR,
            width=1,
        )
        draw.rectangle(
            (x + width, round(top), x + width + band, round(bottom)),
            fill=fill,
            outline=COORDINATE_FRAME_COLOR,
            width=1,
        )

    label_gap = max(3, band // 3)
    for longitude, tick_x in longitude_ticks:
        label = _format_coordinate_label(longitude, "E", "W")
        label_box = draw.textbbox((0, 0), label, font=font)
        label_width = label_box[2] - label_box[0]
        label_height = label_box[3] - label_box[1]
        label_x = tick_x - label_width / 2.0
        draw.text(
            (label_x, y - band - label_gap - label_height - label_box[1]),
            label,
            font=font,
            fill=COORDINATE_FRAME_COLOR,
        )
        draw.text(
            (label_x, y + height + band + label_gap - label_box[1]),
            label,
            font=font,
            fill=COORDINATE_FRAME_COLOR,
        )

    sample_box = font.getbbox("Ag")
    font_height = sample_box[3] - sample_box[1]
    side_label_offset = band + label_gap + max(8, font_height // 2)
    for latitude, tick_y in latitude_ticks:
        label = _format_coordinate_label(latitude, "N", "S")
        _draw_rotated_coordinate_label(
            canvas,
            label,
            (x - side_label_offset, tick_y),
            font,
            90,
        )
        _draw_rotated_coordinate_label(
            canvas,
            label,
            (x + width + side_label_offset, tick_y),
            font,
            -90,
        )

    draw.rectangle(
        (x - band, y - band, x + width + band, y + height + band),
        outline=COORDINATE_FRAME_COLOR,
        width=2,
    )
    draw.rectangle(
        (x, y, x + width, y + height),
        outline=COORDINATE_FRAME_COLOR,
        width=2,
    )


def _nice_scale_distance(target_km: float) -> float:
    if target_km <= 0:
        return 1.0
    exponent = 10 ** math.floor(math.log10(target_km))
    normalized = target_km / exponent
    value = 1 if normalized < 2 else 2 if normalized < 5 else 5
    return value * exponent


def _nice_grid_step(span_degrees: float) -> float:
    """Return a rounded graticule interval giving roughly five gridlines."""
    target = max(abs(span_degrees) / 5.0, 1e-6)
    for step in (0.1, 0.25, 0.5, 1.0, 2.0, 2.5, 5.0, 10.0, 15.0):
        if step >= target:
            return step
    return 20.0


def _representative_scale_denominator(
    bounds: tuple[float, float, float, float],
    map_width: float,
    output_dpi: float,
) -> float:
    """Return the representative fraction at the map centre latitude."""
    if map_width <= 0 or output_dpi <= 0:
        raise ValueError("Lebar peta dan DPI harus lebih besar dari nol.")
    west, south, east, north = bounds
    center_latitude = (south + north) / 2.0
    latitude_radians = math.radians(center_latitude)
    longitude_km_per_degree = (
        111412.84 * math.cos(latitude_radians)
        - 93.5 * math.cos(3.0 * latitude_radians)
        + 0.118 * math.cos(5.0 * latitude_radians)
    ) / 1000.0
    km_per_pixel = longitude_km_per_degree * abs(east - west) / map_width
    return km_per_pixel * output_dpi * 1_000_000.0 / 25.4


def _bounds_for_representative_scale(
    bounds: tuple[float, float, float, float],
    map_size: tuple[int, int],
    denominator: float,
    output_dpi: float,
) -> tuple[float, float, float, float]:
    """Center a Web Mercator viewport at an exact horizontal print scale."""
    width, height = map_size
    if width <= 0 or height <= 0 or output_dpi <= 0:
        raise PublicationMapError("Ukuran peta dan DPI harus lebih besar dari nol.")
    if not math.isfinite(denominator) or not 1_000 <= denominator <= 100_000_000:
        raise PublicationMapError(
            "Penyebut skala harus berada antara 1.000 dan 100.000.000."
        )

    west, south, east, north = bounds
    center_longitude = (west + east) / 2.0
    center_latitude = (south + north) / 2.0
    _, center_y = _mercator_pixel(center_longitude, center_latitude, 0)
    target_aspect = width / height
    km_per_pixel = denominator * 25.4 / (output_dpi * 1_000_000.0)
    fitted = bounds

    # The arithmetic centre latitude used by the representative-fraction
    # formula differs slightly from the Mercator midpoint. A few iterations
    # make the inverse operation stable without changing the chosen centre.
    for _ in range(4):
        latitude_radians = math.radians(center_latitude)
        longitude_km_per_degree = (
            111412.84 * math.cos(latitude_radians)
            - 93.5 * math.cos(3.0 * latitude_radians)
            + 0.118 * math.cos(5.0 * latitude_radians)
        ) / 1000.0
        longitude_span = km_per_pixel * width / longitude_km_per_degree
        if longitude_span >= 350.0:
            raise PublicationMapError(
                "Skala terlalu kecil untuk bidang peta ini; pilih penyebut yang lebih kecil."
            )
        projected_width = longitude_span / 360.0 * 256.0
        projected_height = projected_width / target_aspect
        left = (center_longitude - longitude_span / 2.0 + 180.0) / 360.0 * 256.0
        right = (center_longitude + longitude_span / 2.0 + 180.0) / 360.0 * 256.0
        top = center_y - projected_height / 2.0
        bottom = center_y + projected_height / 2.0
        fitted_west, fitted_north = _inverse_mercator_pixel(left, top, 0)
        fitted_east, fitted_south = _inverse_mercator_pixel(right, bottom, 0)
        fitted = fitted_west, fitted_south, fitted_east, fitted_north
        center_latitude = (fitted_south + fitted_north) / 2.0
    return fitted


def _format_representative_scale(denominator: float) -> str:
    """Format a readable approximate representative fraction in Indonesian."""
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError("Penyebut skala harus berupa angka positif.")
    rounded = int(round(denominator))
    formatted = f"{rounded:,}".replace(",", ".")
    return f"SKALA 1 : {formatted}"


def _draw_scale_bar(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[float, float, float, float],
    map_width: int,
    box: tuple[int, int, int, int],
    scale: float = 1.0,
    output_dpi: float = OUTPUT_DPI,
) -> None:
    """Draw a horizontal scale bar valid at the map centre latitude."""
    west, south, east, north = bounds
    center_latitude = (south + north) / 2.0
    latitude_radians = math.radians(center_latitude)
    longitude_km_per_degree = (
        111412.84 * math.cos(latitude_radians)
        - 93.5 * math.cos(3.0 * latitude_radians)
        + 0.118 * math.cos(5.0 * latitude_radians)
    ) / 1000.0
    km_per_pixel = longitude_km_per_degree * (east - west) / map_width
    target_km = km_per_pixel * box[2] * 0.55
    total_km = _nice_scale_distance(target_km)
    total_pixels = min(box[2], max(1, int(total_km / km_per_pixel)))
    segment_width = total_pixels / 8.0
    box_x, y, box_width, _ = box
    label_font_size = max(10, int(13 * scale), box[2] // 30)
    font = _font(label_font_size)
    half_distance = total_km / 2.0
    first_label = f"{half_distance:g}"
    last_label = f"{half_distance:g} Km"
    first_label_width = draw.textbbox((0, 0), first_label, font=font)[2]
    last_label_width = draw.textbbox((0, 0), last_label, font=font)[2]
    # Centre the complete annotated scale, not only its drawing box.  The
    # wider final "km" label otherwise makes the scale look left-aligned.
    x = (
        box_x
        + box_width / 2.0
        - total_pixels / 2.0
        - (last_label_width - first_label_width) / 4.0
    )
    x = max(
        box_x + first_label_width / 2.0,
        min(x, box_x + box_width - total_pixels - last_label_width / 2.0),
    )
    bar_height = max(7, round(12 * scale))
    bar_outline_width = max(1, round(1.5 * scale))
    # Leave enough *physical* white space for print-resolution output.  The
    # previous 6 px base gap technically cleared the glyphs, but at 300 DPI it
    # disappeared when the page was scaled down for preview and made the
    # labels look fused with the bar.
    label_gap = max(10, round(12 * scale))
    numeric_scale = _format_representative_scale(
        _representative_scale_denominator(bounds, map_width, output_dpi)
    )
    # The heading has to stay visibly larger than the bar labels.  Those grow
    # with the sidebar width, so a fixed heading size ended up *smaller* than
    # its own labels on A2.
    numeric_font = _serif_font(
        max(label_font_size + 2, round(label_font_size * 1.25)),
        bold=True,
    )
    numeric_gap = label_gap + max(14, int(16 * scale))
    draw.text(
        (box_x + box_width / 2.0, y - numeric_gap),
        numeric_scale,
        font=numeric_font,
        fill="#111111",
        anchor="mb",
    )
    for index in range(8):
        fill = "#111111" if index % 2 == 0 else "#ffffff"
        draw.rectangle(
            (
                x + index * segment_width,
                y,
                x + (index + 1) * segment_width,
                y + bar_height,
            ),
            fill=fill,
            outline="#111111",
            width=bar_outline_width,
        )
    scale_labels = ((0, first_label), (4, "0"), (8, last_label))
    for index, label in scale_labels:
        draw.text(
            (x + index * segment_width, y - label_gap),
            label,
            font=font,
            fill="#111111",
            anchor="mb",
        )


def _basemap_source_line(attribution: str) -> str:
    """Render one unambiguous basemap credit line.

    Attributions already carry their own layer name ("Topografi: Esri ..."), so
    prefixing them blindly produced "Basemap: Topografi: Esri ...", which reads
    as if "Topografi" were the provider.
    """
    text = attribution.strip()
    if not text:
        return "Basemap: tidak tersedia"
    name, separator, credit = text.partition(":")
    if separator and credit.strip():
        return f"Basemap {name.strip()}: {credit.strip()}"
    return f"Basemap: {text}"


def _publication_source_lines(
    source_text: str,
    attribution: str,
    *,
    show_basemap: bool,
    show_earthquakes: bool = True,
    show_faults: bool,
    show_trenches: bool,
    show_slabs: bool,
    show_population_density: bool,
    show_provinces: bool,
    show_inset: bool,
    fault_geojson: dict[str, Any],
    trench_geojson: dict[str, Any] | None,
    slab_geojson: dict[str, Any] | None,
    population_density_mode: str = "province",
    hazard_source: str | None = None,
) -> list[str]:
    """List only sources for data and layers present in the publication map.

    A layer that is switched off must not be credited: a sheet plotting no
    epicentres that still cites an earthquake catalogue invites the reader to
    assume the map shows every event in it.
    """
    lines: list[str] = []
    if show_earthquakes:
        earthquake_source = source_text.strip() or "Database Interaktif Gempa Indonesia"
        if earthquake_source.lower().startswith(("data gempa:", "gempa:")):
            lines.append(earthquake_source)
        else:
            lines.append(f"Data gempa: {earthquake_source}")

    if show_basemap:
        if attribution.startswith("VersaTiles Shortbread"):
            shortbread_source = (
                "Basemap: VersaTiles Shortbread; data © OpenStreetMap; "
                "landcover © ESA WorldCover 2021"
            )
            if "Relief:" in attribution:
                shortbread_source += "; relief © Esri"
            lines.append(shortbread_source)
        else:
            lines.append(_basemap_source_line(attribution))
    if show_faults and fault_geojson.get("features"):
        lines.append("Sesar: PUSGEN 2024 melalui layanan GIS BNPB InaRISK")
    # Source labels mirror the legend wording so a reader can match each credit
    # to the symbol it explains.
    if show_trenches and trench_geojson is not None and trench_geojson.get("features"):
        lines.append("Tunjaman: Slab2 USGS (Hayes dkk., 2018)")
    if show_slabs and slab_geojson is not None and slab_geojson.get("features"):
        lines.append("Kontur slab: Slab2 USGS (Hayes dkk., 2018)")
    if show_population_density:
        if population_density_mode == "district":
            lines.append(
                "Kepadatan penduduk: BPS kabupaten/kota, data kecamatan 2023-2025 "
                "(choropleth kelas lokal)"
            )
        else:
            lines.append(
                "Kepadatan penduduk: BPS kabupaten/kota, data kecamatan 2023-2025 "
                "(ordinary kriging per provinsi)"
            )
    if hazard_source:
        lines.append(hazard_source)
    if show_provinces or show_inset:
        # MIT attribution is an obligation, not a courtesy. The BIG provenance
        # is what the upstream repository states, so the wording keeps that
        # hedge instead of asserting it as verified fact.
        lines.append(
            "Batas administrasi: GeoJSON 38 Provinsi (© 2024 Ardian Saputra "
            "Hasibuan, MIT); repositori menyebut BIG sebagai sumber awal"
        )
    return lines


def _draw_population_density_legend(
    draw: ImageDraw.ImageDraw,
    side_x: int,
    side_width: int,
    legend_y: int,
    scale: float,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    small_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    density_breaks: Collection[float],
    density_colors: Collection[str] = POPULATION_DENSITY_COLORS,
    *,
    compact: bool = False,
) -> int:
    """Draw an adaptive density legend and return the next y position."""
    colors = list(density_colors)
    return _draw_class_legend(
        draw, side_x, side_width, legend_y, scale, label_font, small_font,
        "Kepadatan penduduk (jiwa/km2)",
        colors,
        get_population_density_legend_labels(density_breaks, colors),
        compact=compact,
    )


def _draw_class_legend(
    draw: ImageDraw.ImageDraw,
    side_x: int,
    side_width: int,
    legend_y: int,
    scale: float,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    small_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    heading: str,
    colors: Collection[str],
    legend_labels: Collection[str],
    *,
    compact: bool = False,
) -> int:
    """Draw a heading plus colour-class swatches; return the next y position."""
    draw.text(
        (side_x + int(24 * scale), legend_y),
        heading,
        font=label_font,
        fill="#111827",
    )
    legend_y += int((22 if compact else 27) * scale)
    columns = 3 if compact else 2
    horizontal_padding = int((36 if compact else 48) * scale)
    column_width = (side_width - horizontal_padding) / columns
    swatch_size = int((11 if compact else 14) * scale)
    row_height = int((18 if compact else 24) * scale)
    colors = list(colors)
    for index, (color, label) in enumerate(
        zip(colors, legend_labels)
    ):
        column = index % columns
        row = index // columns
        entry_x = side_x + int((18 if compact else 24) * scale) + column * column_width
        entry_y = legend_y + row * row_height
        draw.rectangle(
            (
                entry_x,
                entry_y,
                entry_x + swatch_size,
                entry_y + swatch_size,
            ),
            fill=color,
            outline="#1e3a8a",
            width=1,
        )
        draw.text(
            (entry_x + swatch_size + int((5 if compact else 7) * scale), entry_y - int(2 * scale)),
            label,
            font=small_font,
            fill="#111827",
        )
    row_count = math.ceil(len(colors) / columns)
    return legend_y + int(row_count * row_height + (4 if compact else 6) * scale)


_LABEL_CANDIDATE_GRID = 7
_LABEL_CANDIDATE_LIMIT = 12


def _fitted_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    available_width: int,
    size: int,
    *,
    bold: bool = False,
    minimum: int = 8,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Largest font at or below ``size`` whose ``text`` still fits."""
    while size > minimum:
        font = _font(size, bold=bold)
        if draw.textlength(text, font=font) <= available_width:
            return font
        size -= 1
    return _font(minimum, bold=bold)


RTRW_PROJECTION_ROWS = (
    ("Proyeksi", "Transverse Mercator"),
    ("Sistem Grid", "Grid Geografi dan Grid Universal Transverse Mercator"),
    ("Datum Horizontal", "WGS 1984"),
)


def _draw_sidebar_rule(
    draw: ImageDraw.ImageDraw,
    side_x: int,
    side_width: int,
    y: int,
    scale: float,
) -> None:
    """Full-width divider between sidebar blocks, as on an official sheet."""
    draw.line(
        (side_x, y, side_x + side_width, y),
        fill="#111111",
        width=max(1, round(1.5 * scale)),
    )


def _draw_rtrw_boxed_text(
    draw: ImageDraw.ImageDraw,
    side_x: int,
    side_width: int,
    top: int,
    scale: float,
    lines: Collection[str],
    *,
    size: int,
    render: bool = True,
) -> int:
    """Centre a stack of lines in a ruled block; returns the block's bottom.

    ``render=False`` measures without drawing, so the sidebar can budget its
    vertical space before it starts painting.
    """
    text_lines = [line for line in (str(value).strip() for value in lines) if line]
    if not text_lines:
        return top
    font = _serif_font(max(11, round(size * scale)), bold=True)
    line_height = draw.textbbox((0, 0), "Ag", font=font)[3] + max(4, round(6 * scale))
    padding = max(6, round(10 * scale))
    bottom = top + len(text_lines) * line_height + padding * 2
    if render:
        for index, line in enumerate(text_lines):
            draw.text(
                (side_x + side_width // 2, top + padding + index * line_height),
                _ellipsize_text(draw, line, font, side_width - round(24 * scale)),
                font=font,
                fill="#111111",
                anchor="ma",
            )
        _draw_sidebar_rule(draw, side_x, side_width, bottom, scale)
    return bottom


def _draw_rtrw_projection_block(
    draw: ImageDraw.ImageDraw,
    side_x: int,
    side_width: int,
    top: int,
    scale: float,
    *,
    render: bool = True,
) -> int:
    """Projection / grid / datum rows joined by dotted leaders."""
    label_font = _font(max(9, round(12 * scale)))
    padding = max(6, round(9 * scale))
    inner_x = side_x + round(16 * scale)
    inner_width = side_width - round(32 * scale)
    label_width = int(inner_width * 0.40)
    row_height = draw.textbbox((0, 0), "Ag", font=label_font)[3] + max(5, round(7 * scale))
    bottom = top + padding * 2 + row_height * len(RTRW_PROJECTION_ROWS)
    if not render:
        return bottom
    value_x = inner_x + label_width
    value_width = inner_width - label_width
    for index, (label, value) in enumerate(RTRW_PROJECTION_ROWS):
        y = top + padding + index * row_height
        draw.text((inner_x, y), label, font=label_font, fill="#111111")
        leader_start = inner_x + draw.textlength(label, font=label_font) + round(4 * scale)
        leader_end = value_x - round(5 * scale)
        dot_width = max(1.0, draw.textlength(".", font=label_font))
        dot_count = int((leader_end - leader_start) / dot_width)
        if dot_count > 0:
            draw.text(
                (leader_start, y), "." * dot_count, font=label_font, fill="#6b7280"
            )
        # Long values shrink rather than get cut: the datum line is the whole
        # point of the block.
        draw.text(
            (value_x, y),
            value,
            font=_fitted_font(draw, value, value_width, max(9, round(12 * scale))),
            fill="#111111",
        )
    _draw_sidebar_rule(draw, side_x, side_width, bottom, scale)
    return bottom


def _draw_north_arrow(
    canvas: Image.Image,
    box: tuple[int, int, int, int],
) -> None:
    x, y, width, height = box
    # Draw on a tightly cropped transparent layer so the compass never adds a
    # pale rectangular panel over the basemap.
    arrow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(arrow, "RGBA")
    center_x = width / 2
    font = _font(max(24, width // 3), bold=True)
    draw.text((center_x - width * 0.12, 0), "N", font=font, fill="#111111")
    top = height * 0.25
    bottom = height - 1
    draw.polygon(
        [(center_x, top), (width * 0.16, bottom), (center_x, bottom * 0.84)],
        fill="#111111",
        outline="#111111",
    )
    draw.polygon(
        [(center_x, top), (width * 0.84, bottom), (center_x, bottom * 0.84)],
        fill="#ffffff",
        outline="#111111",
    )
    canvas.paste(arrow, (x, y), arrow)


def _overview_bounds(
    current_bounds: tuple[float, float, float, float],
    size: tuple[int, int],
    expand: float = 1.3,
    min_span: float = 2.5,
) -> tuple[float, float, float, float]:
    """Regional locator window around the data extent, matched to box aspect.

    Stays tight around the coverage area (current_bounds) with modest
    padding rather than always zooming out to fit all of Indonesia - the
    box only widens as far as the aspect-ratio fit requires.
    """
    west, south, east, north = current_bounds
    center_longitude = (west + east) / 2.0
    center_latitude = (south + north) / 2.0
    box_aspect = size[0] / size[1]
    span_longitude = max((east - west) * expand, min_span)
    span_latitude = max((north - south) * expand, min_span / box_aspect)
    if span_longitude / span_latitude < box_aspect:
        span_longitude = span_latitude * box_aspect
    else:
        span_latitude = span_longitude / box_aspect
    return (
        center_longitude - span_longitude / 2.0,
        center_latitude - span_latitude / 2.0,
        center_longitude + span_longitude / 2.0,
        center_latitude + span_latitude / 2.0,
    )


def _render_overview_map(
    province_geojson: dict[str, Any],
    current_bounds: tuple[float, float, float, float],
    size: tuple[int, int],
    expand: float = 1.3,
    min_span: float = 2.5,
) -> Image.Image:
    """Render a regional locator inset; geometry is clipped to the inset image."""
    width, height = size
    image = Image.new("RGB", (width, height), "#cfe3ec")
    draw = ImageDraw.Draw(image)
    west, south, east, north = _overview_bounds(
        current_bounds, size, expand=expand, min_span=min_span
    )

    def project(longitude: float, latitude: float) -> tuple[float, float]:
        return (
            (longitude - west) / (east - west) * width,
            (north - latitude) / (north - south) * height,
        )

    for feature in province_geojson.get("features", []):
        for ring in _iter_polygon_rings(feature.get("geometry") or {}):
            points = [project(float(point[0]), float(point[1])) for point in ring]
            if len(points) >= 3:
                draw.polygon(points, fill="#e9f1e0")
    for feature in province_geojson.get("features", []):
        for ring in _iter_polygon_rings(feature.get("geometry") or {}):
            points = [project(float(point[0]), float(point[1])) for point in ring]
            if len(points) >= 2:
                draw.line(points, fill="#6b9a78", width=1)

    current_west, current_south, current_east, current_north = current_bounds
    top_left = project(current_west, current_north)
    bottom_right = project(current_east, current_south)
    draw.rectangle(
        (
            min(top_left[0], bottom_right[0]),
            min(top_left[1], bottom_right[1]),
            max(top_left[0], bottom_right[0]),
            max(top_left[1], bottom_right[1]),
        ),
        outline="#dc2626",
        width=max(2, width // 80),
    )
    draw.rectangle((0, 0, width - 1, height - 1), outline="#111827", width=2)
    return image


def _draw_locator_inset(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    province_geojson: dict[str, Any],
    bounds: tuple[float, float, float, float],
    sidebar_box: tuple[int, int, int, int],
    inset_top: int,
    source_y: int,
    scale: float,
    label_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    expand: float = 1.3,
    min_span: float = 2.5,
) -> str:
    """Draw the locator exclusively in the publication sidebar."""
    side_x, side_y, side_width, _ = sidebar_box
    heading_height = int(20 * scale)
    inset_bottom = source_y - int(14 * scale)
    inset_top = max(side_y, inset_top)
    available_sidebar_height = inset_bottom - inset_top - heading_height
    inset_width = side_width - int(44 * scale)
    # Never draw past inset_bottom: an overloaded legend leaves too little
    # room, and a sliver of a map is worth less than the space it would steal
    # from the source block below.
    inset_height = min(int(205 * scale), available_sidebar_height)
    if inset_height < int(40 * scale):
        return "none"
    draw.text(
        (side_x + int(24 * scale), inset_top),
        "Lokasi Cakupan",
        font=label_font,
        fill="#111827",
    )
    overview = _render_overview_map(
        province_geojson,
        bounds,
        (inset_width, inset_height),
        expand=expand,
        min_span=min_span,
    )
    canvas.paste(overview, (side_x + int(22 * scale), inset_top + heading_height))
    return "sidebar"


def _load_logo(logo_bytes: bytes | None, max_size: tuple[int, int]) -> Image.Image | None:
    if not logo_bytes:
        return None
    try:
        logo = Image.open(BytesIO(logo_bytes)).convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise PublicationMapError("Logo harus berupa PNG, JPG, atau WEBP yang valid.") from exc
    logo.thumbnail(max_size, Image.Resampling.LANCZOS)
    return logo


def render_publication_map(
    df: pd.DataFrame,
    fault_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
    *,
    district_geojson: dict[str, Any] | None = None,
    trench_geojson: dict[str, Any] | None = None,
    slab_geojson: dict[str, Any] | None = None,
    population_density_geojson: dict[str, Any] | None = None,
    hazard_layer: PublicationHazardLayer | None = None,
    title: str,
    institution_name: str,
    logo_bytes: bytes | None = None,
    second_logo_bytes: bytes | None = None,
    logo_scale_percent: int = 0,
    marker_scale_percent: int = 100,
    padding_percent: int = 12,
    page_size: str = "A4 Lanskap",
    output_dpi: float = OUTPUT_DPI,
    show_basemap: bool = True,
    basemap_name: str = BASEMAP_OPENTOPO,
    show_shortbread_roads: bool = False,
    show_shortbread_rivers: bool = True,
    show_shortbread_topography: bool = False,
    show_basemap_legend: bool = True,
    show_faults: bool = True,
    show_earthquakes: bool = True,
    show_trenches: bool = True,
    show_slabs: bool = False,
    show_stations: bool = False,
    station_records: Collection[tuple[Any, ...]] | None = None,
    show_population_density: bool = False,
    show_population_density_legend: bool = True,
    show_provinces: bool = True,
    show_region_labels: bool = True,
    show_inset: bool = True,
    fault_names: Collection[str] | None = None,
    fault_feature_indexes: Collection[int] | None = None,
    province_names: Collection[str] | None = None,
    district_names: Collection[str] | None = None,
    source_text: str = "Data gempa: Database Interaktif Gempa Indonesia",
    map_snapshot_bytes: bytes | None = None,
    _map_only_size: tuple[int, int] | None = None,
    basemap_progress_callback: BasemapProgressCallback | None = None,
    map_type_label: str = "",
    representative_scale_denominator: int | None = None,
    template_name: str = PUBLICATION_TEMPLATE_LEGACY,
    selection_bounds: tuple[float, float, float, float] | None = None,
) -> tuple[bytes, bytes]:
    page_pixels = get_publication_page_size(page_size, output_dpi)
    # Turning the layer off hides the markers *and* the magnitude/depth keys, and
    # stops the epicentres from framing the view.
    has_earthquakes = show_earthquakes and not df.empty
    if selection_bounds is not None:
        # A box drawn on the distribution map is an explicit area of interest,
        # so it frames the sheet even when the points inside it are sparse.
        base_bounds = _selection_frame_bounds(selection_bounds, padding_percent)
    elif has_earthquakes:
        base_bounds = _map_bounds(df, padding_percent)
    else:
        base_bounds = _region_bounds(
            province_geojson,
            district_geojson,
            province_names,
            district_names,
            padding_percent,
        )
        if base_bounds is None:
            raise PublicationMapError(
                "Tidak ada titik gempa yang dipetakan"
                + (
                    " karena layer titik gempa dimatikan"
                    if not show_earthquakes
                    else ""
                )
                + ". Pilih provinsi atau kabupaten/kota untuk membuat peta "
                "wilayah."
            )

    if _map_only_size is None:
        page_width, page_height = page_pixels
        scale, header_height, margin, map_box, sidebar_box = _publication_layout(
            page_size,
            output_dpi,
            template_name,
        )
    else:
        page_width, page_height = _map_only_size
        scale = page_pixels[0] / PAGE_SIZES["A4 Lanskap"][0]
        header_height = 0
        margin = 0
        map_box = (0, 0, page_width, page_height)
        sidebar_box = (page_width, 0, 0, 0)
    max_tile_count = _publication_tile_limit(
        output_dpi,
        map_size=(map_box[2], map_box[3]),
    )
    bounds = _fit_bounds_to_web_mercator_aspect(
        base_bounds,
        (map_box[2], map_box[3]),
    )
    if representative_scale_denominator is not None:
        bounds = _bounds_for_representative_scale(
            bounds,
            (map_box[2], map_box[3]),
            representative_scale_denominator,
            output_dpi,
        )

    canvas = Image.new("RGB", (page_width, page_height), "#ffffff")
    draw = ImageDraw.Draw(canvas, "RGBA")
    if _map_only_size is None and template_name != PUBLICATION_TEMPLATE_RTRW:
        draw.rectangle((0, 0, page_width, header_height), fill=PUBLICATION_HEADER_FILL)
        draw.line(
            (margin, header_height - 2, page_width - margin, header_height - 2),
            fill="#111111",
            width=max(1, int(1.5 * scale)),
        )
        title_rows = max(1, len([line for line in title.splitlines() if line.strip()]))
        title_size = 48 if title_rows == 1 else 39 if title_rows == 2 else 32
        title_font = (
            _serif_font(max(28, int(title_size * scale)), bold=True)
            if template_name == PUBLICATION_TEMPLATE_BANDUNG
            else _font(max(28, int(title_size * scale)), bold=True)
        )
        _draw_centered_wrapped_text(
            draw,
            title.upper(),
            (margin, 8, page_width - margin * 2, header_height - 16),
            title_font,
            PUBLICATION_HEADER_TEXT,
        )

    marker_scale = marker_scale_percent / 100.0 * scale
    population_density_colors = (
        POPULATION_DENSITY_DISTRICT_COLORS
        if district_names
        else POPULATION_DENSITY_COLORS
    )
    population_density_breaks = get_population_density_breaks(
        population_density_geojson,
        class_count=len(population_density_colors),
    )
    if map_snapshot_bytes is not None:
        try:
            map_snapshot = Image.open(BytesIO(map_snapshot_bytes)).convert("RGB")
        except (UnidentifiedImageError, OSError) as exc:
            raise PublicationMapError("Snapshot peta tidak valid.") from exc
        if map_snapshot.size != (map_box[2], map_box[3]):
            map_snapshot = map_snapshot.resize(
                (map_box[2], map_box[3]),
                Image.Resampling.LANCZOS,
            )
        canvas.paste(map_snapshot, (map_box[0], map_box[1]))
        if show_faults:
            _draw_snapshot_fault_labels(
                canvas,
                bounds,
                map_box,
                scale,
                fault_geojson,
                fault_names,
                fault_feature_indexes,
            )
        if not show_basemap:
            attribution = "Basemap dinonaktifkan"
        elif basemap_name == BASEMAP_SHORTBREAD:
            attribution = (
                f"{get_basemap_attribution(BASEMAP_RELIEF, html=False)} | "
                f"Mode publikasi raster untuk {BASEMAP_SHORTBREAD}"
            )
        else:
            attribution = get_basemap_attribution(
                basemap_name,
                show_shortbread_topography=show_shortbread_topography,
                html=False,
            )
    elif show_basemap:
        basemap, attribution, zoom = _render_basemap(
            bounds,
            (map_box[2], map_box[3]),
            basemap_name,
            show_shortbread_roads=show_shortbread_roads,
            show_shortbread_rivers=show_shortbread_rivers,
            show_shortbread_topography=show_shortbread_topography,
            progress_callback=basemap_progress_callback,
            max_tile_count=max_tile_count,
        )
        canvas.paste(basemap, (map_box[0], map_box[1]))
    else:
        draw.rectangle(
            (map_box[0], map_box[1], map_box[0] + map_box[2], map_box[1] + map_box[3]),
            fill="#e9f2f5",
        )
        attribution = "Basemap dinonaktifkan"
        zoom = _choose_zoom(bounds, map_box[2], map_box[3], max_tile_count)

    if map_snapshot_bytes is None:
        overlay = Image.new("RGBA", (map_box[2], map_box[3]), (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay, "RGBA")
        project = _projector(bounds, zoom, (0, 0, map_box[2], map_box[3]))
        if show_population_density and population_density_geojson is not None:
            if district_names:
                for feature in population_density_geojson.get("features", []):
                    density = float(
                        (feature.get("properties") or {}).get(
                            "kepadatan_penduduk_per_km2", 0
                        )
                    )
                    fill = ImageColor.getrgb(
                        get_population_density_color(
                            density,
                            population_density_breaks,
                            population_density_colors,
                        )
                    ) + (224,)
                    for ring in _iter_polygon_rings(feature.get("geometry") or {}):
                        points = [
                            project(float(point[0]), float(point[1]))
                            for point in ring
                        ]
                        if len(points) >= 3:
                            overlay_draw.polygon(
                                points,
                                fill=fill,
                                outline=(17, 24, 39, 210),
                                width=max(1, int(scale)),
                            )
            else:
                for surface in build_population_density_surfaces(
                    population_density_geojson,
                    province_geojson,
                    density_breaks=population_density_breaks,
                ):
                    (south, west), (north, east) = surface["bounds"]
                    left, top = project(float(west), float(north))
                    right, bottom = project(float(east), float(south))
                    target_width = max(1, round(abs(right - left)))
                    target_height = max(1, round(abs(bottom - top)))
                    raster = surface["image"].resize(
                        (target_width, target_height),
                        Image.Resampling.BILINEAR,
                    )
                    destination_x = round(min(left, right))
                    destination_y = round(min(top, bottom))
                    overlay.alpha_composite(
                        raster,
                        dest=(destination_x, destination_y),
                    )
                overlay_draw = ImageDraw.Draw(overlay, "RGBA")
        if hazard_layer is not None:
            overlay.alpha_composite(
                _render_hazard_overlay(hazard_layer, bounds, zoom, overlay.size)
            )
            overlay_draw = ImageDraw.Draw(overlay, "RGBA")
        if district_geojson is not None and district_names:
            selected_districts = set(district_names)
            # Draw the district focus mask separately, then alpha-composite it.
            # Drawing a low-alpha fill directly with ImageDraw on ``overlay``
            # replaces the existing RGBA pixels instead of blending them. That
            # previously erased the district-level population choropleth below.
            district_focus = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
            district_focus_draw = ImageDraw.Draw(district_focus, "RGBA")
            for feature in district_geojson.get("features", []):
                is_selected = (
                    (feature.get("properties") or {}).get("WADMKK")
                    in selected_districts
                )
                for ring in _iter_polygon_rings(feature.get("geometry") or {}):
                    points = [project(float(point[0]), float(point[1])) for point in ring]
                    if len(points) < 3:
                        continue
                    district_focus_draw.polygon(
                        points,
                        fill=(255, 255, 255, 3) if is_selected else (209, 213, 219, 116),
                        outline=(249, 115, 22, 255) if is_selected else (100, 116, 139, 56),
                    )
            overlay.alpha_composite(district_focus)
            overlay_draw = ImageDraw.Draw(overlay, "RGBA")
        if show_provinces:
            for feature in province_geojson.get("features", []):
                for ring in _iter_polygon_rings(feature.get("geometry") or {}):
                    points = [project(float(point[0]), float(point[1])) for point in ring]
                    if len(points) >= 2:
                        overlay_draw.line(
                            points,
                            fill="#064e3b",
                            width=max(2, int(2.2 * scale)),
                        )

        if show_region_labels:
            selected_provinces = set(province_names or [])
            province_font = _font(max(12, max(14, map_box[2] // 68)), bold=True)
            for feature in province_geojson.get("features", []):
                properties = feature.get("properties") or {}
                name = str(properties.get("PROVINSI") or "").strip()
                if not name or (selected_provinces and name not in selected_provinces):
                    continue
                ring = _largest_outer_ring(feature.get("geometry") or {})
                if not ring:
                    continue
                latitude, longitude = _ring_centroid(ring)
                label_x, label_y = project(longitude, latitude)
                if 0 <= label_x <= map_box[2] and 0 <= label_y <= map_box[3]:
                    overlay_draw.text(
                        (label_x, label_y),
                        name,
                        font=province_font,
                        fill="#000000",
                        anchor="mm",
                        stroke_width=max(1, int(2 * scale)),
                        stroke_fill="#ffffff",
                    )

            selected_districts = set(district_names or [])
            if district_geojson is not None and (selected_districts or zoom >= 8):
                district_font = _font(max(10, max(12, map_box[2] // 82)), bold=True)
                for feature in district_geojson.get("features", []):
                    properties = feature.get("properties") or {}
                    name = str(properties.get("WADMKK") or "").strip()
                    province = str(properties.get("WADMPR") or "").strip()
                    if selected_provinces and province not in selected_provinces:
                        continue
                    if selected_districts and name not in selected_districts:
                        continue
                    ring = _largest_outer_ring(feature.get("geometry") or {})
                    if not name or not ring:
                        continue
                    latitude, longitude = _ring_centroid(ring)
                    label_x, label_y = project(longitude, latitude)
                    if 0 <= label_x <= map_box[2] and 0 <= label_y <= map_box[3]:
                        overlay_draw.text(
                            (label_x, label_y),
                            name,
                            font=district_font,
                            fill="#000000",
                            anchor="mm",
                            stroke_width=max(1, int(2 * scale)),
                            stroke_fill="#ffffff",
                        )

        selected_faults = None if fault_names is None else set(fault_names)
        selected_fault_indexes = (
            None
            if fault_feature_indexes is None
            else set(fault_feature_indexes)
        )
        if show_faults:
            visible_fault_features: list[dict[str, Any]] = []
            for feature_index, feature in enumerate(fault_geojson.get("features", [])):
                if (
                    selected_fault_indexes is not None
                    and feature_index not in selected_fault_indexes
                ):
                    continue
                if (
                    selected_faults is not None
                    and feature.get("properties", {}).get("Name") not in selected_faults
                ):
                    continue
                visible_fault_features.append(feature)
                type_code = feature.get("properties", {}).get("Type")
                fault_class = classify_fault_type(type_code)
                for line in _iter_lines(feature.get("geometry") or {}):
                    points = [project(float(point[0]), float(point[1])) for point in line]
                    _draw_fault_polyline(
                        overlay_draw,
                        points,
                        fault_class,
                        scale,
                        type_code,
                    )
            fault_label_font = _font(max(8, int(9 * scale)), bold=True)
            for name, latitude, longitude in get_fault_label_records(
                {"type": "FeatureCollection", "features": visible_fault_features}
            ):
                label_x, label_y = project(longitude, latitude)
                overlay_draw.text(
                    (label_x, label_y),
                    name,
                    font=fault_label_font,
                    fill="#000000",
                    anchor="mm",
                    stroke_width=max(1, int(2 * scale)),
                    stroke_fill="#ffffff",
                )

        if show_trenches and trench_geojson is not None:
            for feature in trench_geojson.get("features", []):
                for line in _iter_lines(feature.get("geometry") or {}):
                    points = [project(float(point[0]), float(point[1])) for point in line]
                    _draw_trench_polyline(overlay_draw, points, scale)

        if show_slabs and slab_geojson is not None:
            for feature in slab_geojson.get("features", []):
                for line in _iter_lines(feature.get("geometry") or {}):
                    points = [project(float(point[0]), float(point[1])) for point in line]
                    if len(points) >= 2:
                        _draw_dashed_line(
                            overlay_draw,
                            points,
                            SLAB_CONTOUR_COLOR,
                            max(1, int(1.4 * scale)),
                            max(4, int(6 * scale)),
                        )

        if show_stations and station_records:
            station_radius = max(4.0, 6.0 * scale)
            for record in station_records:
                if len(record) < 2:
                    continue
                try:
                    latitude = float(record[0])
                    longitude = float(record[1])
                except (TypeError, ValueError):
                    continue
                x, y = project(longitude, latitude)
                if not (-station_radius <= x <= map_box[2] + station_radius):
                    continue
                if not (-station_radius <= y <= map_box[3] + station_radius):
                    continue
                overlay_draw.polygon(
                    (
                        (x, y - station_radius),
                        (x - station_radius * 0.9, y + station_radius * 0.72),
                        (x + station_radius * 0.9, y + station_radius * 0.72),
                    ),
                    fill=SEISMIC_STATION_COLOR,
                    outline=SEISMIC_STATION_BORDER,
                )

        for row in df.itertuples(index=False) if has_earthquakes else ():
            x, y = project(float(row.bujur), float(row.lintang))
            fill_color, border_color, _ = get_depth_style(float(row.kedalaman))
            radius = _publication_magnitude_radius(
                float(row.magnitudo),
                marker_scale,
            )
            overlay_draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=fill_color,
                outline=border_color,
                width=max(1, int(1.5 * scale)),
            )

        canvas.paste(overlay, (map_box[0], map_box[1]), overlay)
    if _map_only_size is not None:
        output = BytesIO()
        canvas.save(output, format="PNG", optimize=True)
        return output.getvalue(), b""
    _draw_coordinate_grid(canvas, draw, bounds, map_box)
    _draw_north_arrow(
        canvas,
        (
            map_box[0] + map_box[2] - int(105 * scale),
            map_box[1] + int(28 * scale),
            int(65 * scale),
            int(105 * scale),
        ),
    )

    side_x, side_y, side_width, side_height = sidebar_box
    draw.rectangle(
        (side_x, side_y, side_x + side_width, side_y + side_height),
        fill="#ffffff",
        outline="#111111",
        width=max(2, int(3 * scale)),
    )
    base_logo_area_height = int(90 * scale)
    logo_top = side_y + int(24 * scale)

    # Size the institution block to the text it actually holds instead of a
    # fixed slot: a one-line name then frees vertical room that the logo and
    # the locator inset below can use.
    institution_rows = max(
        1,
        len([line for line in institution_name.splitlines() if line.strip()]),
    )
    institution_size = 22 if institution_rows == 1 else 19 if institution_rows == 2 else 17
    institution_font = _font(max(15, int(institution_size * scale)), bold=True)
    institution_lines = _wrap_centered_lines(
        draw,
        institution_name or "Nama Instansi",
        side_width - 32,
        institution_font,
    )
    institution_line_height = draw.textbbox((0, 0), "Ag", font=institution_font)[3] + max(
        4, int(76 * scale) // 40
    )
    institution_text_height = max(
        int(34 * scale), len(institution_lines) * institution_line_height + int(10 * scale)
    )
    # _draw_scale_bar hangs its labels 23px above the bar's y, so the block
    # must clear that overhang as well as the text itself.
    institution_block_height = institution_text_height + int(58 * scale)
    rtrw_title_lines = [line for line in title.splitlines() if line.strip()] or [title]
    rtrw_type_lines = [map_type_label] if str(map_type_label).strip() else []
    rtrw_extra_height = 0
    if template_name == PUBLICATION_TEMPLATE_RTRW:
        # Measure the identity blocks up front so the legend and locator below
        # are budgeted against the space they actually leave.
        measured = _draw_rtrw_boxed_text(
            draw, side_x, side_width, 0, scale, rtrw_title_lines, size=19, render=False
        )
        measured = _draw_rtrw_boxed_text(
            draw, side_x, side_width, measured, scale, rtrw_type_lines, size=23,
            render=False,
        )
        rtrw_extra_height = _draw_rtrw_projection_block(
            draw, side_x, side_width, measured, scale, render=False
        )
    legend_basemap_name = (
        BASEMAP_RELIEF if basemap_name == BASEMAP_SHORTBREAD else basemap_name
    )

    normal_legend_block = _sidebar_legend_block_height(
        scale,
        marker_scale,
        institution_block_height + rtrw_extra_height,
        show_basemap=show_basemap,
        basemap_name=legend_basemap_name,
        show_basemap_legend=show_basemap_legend,
        show_shortbread_roads=show_shortbread_roads,
        show_shortbread_rivers=show_shortbread_rivers,
        show_shortbread_topography=show_shortbread_topography,
        show_trenches=show_trenches,
        trench_present=trench_geojson is not None,
        show_faults=show_faults,
        show_slabs=show_slabs,
        slab_present=slab_geojson is not None,
        show_provinces=show_provinces,
        show_population_density=show_population_density,
        show_population_density_legend=show_population_density_legend,
        show_stations=show_stations,
        density_color_count=len(population_density_colors),
        hazard_class_count=len(hazard_layer.colors) if hazard_layer else 0,
        has_earthquakes=has_earthquakes,
    )
    compact_legends = _should_compact_sidebar_legends(
        side_y=side_y,
        side_height=side_height,
        scale=scale,
        logo_top=logo_top,
        base_logo_area_height=base_logo_area_height,
        normal_legend_block_height=normal_legend_block,
    )
    legend_block = (
        _sidebar_legend_block_height(
            scale,
            marker_scale,
            institution_block_height,
            show_basemap=show_basemap,
            basemap_name=legend_basemap_name,
            show_basemap_legend=show_basemap_legend,
            show_shortbread_roads=show_shortbread_roads,
            show_shortbread_rivers=show_shortbread_rivers,
            show_shortbread_topography=show_shortbread_topography,
            show_trenches=show_trenches,
            trench_present=trench_geojson is not None,
            show_faults=show_faults,
            show_slabs=show_slabs,
            slab_present=slab_geojson is not None,
            show_provinces=show_provinces,
            show_population_density=show_population_density,
            show_population_density_legend=show_population_density_legend,
            show_stations=show_stations,
            density_color_count=len(population_density_colors),
            hazard_class_count=len(hazard_layer.colors) if hazard_layer else 0,
            compact=True,
            has_earthquakes=has_earthquakes,
        )
        if compact_legends
        else normal_legend_block
    )

    logo_sources = [source for source in (logo_bytes, second_logo_bytes) if source]
    if logo_sources:
        # logo_scale_percent is a 0-100 intensity dial, not a percentage of
        # original size: 0 keeps the standard logo footprint, 100 grows both
        # the reserved sidebar slot and the logo itself up to ~3x. The
        # reserved slot grows together with the logo so nothing below it
        # (institution name, legend) ever overlaps.
        growth = max(0.0, min(1.0, logo_scale_percent / 100.0))
        requested_area = int(base_logo_area_height * (1 + growth * LOGO_MAX_GROWTH))
        # Adaptive cap: however large the slider asks, never push the
        # full-width "Lokasi Cakupan" locator below a comfortable height -
        # otherwise a busy legend (e.g. the density layer) leaves it a thin,
        # zoomed-out strip. The logo simply grows into whatever space the
        # active legend layers leave free.
        inset_source_y = side_y + side_height - int(150 * scale)
        inset_target_height = int(130 * scale)
        # inset_top = logo_top + logo_area_height + 10 + legend_block + 6;
        # available = (inset_source_y - 14) - inset_top - heading(20).
        max_logo_area = (
            inset_source_y
            - logo_top
            - legend_block
            - inset_target_height
            - int(50 * scale)
        )
        logo_area_height = max(
            base_logo_area_height, min(requested_area, max_logo_area)
        )
        logo_gap = int(18 * scale)
        logo_padding = int(24 * scale)
        slot_width = (
            side_width
            - logo_padding * 2
            - logo_gap * (len(logo_sources) - 1)
        ) // len(logo_sources)
        width_ratio_base = 0.58 if len(logo_sources) == 1 else 0.92
        width_ratio = width_ratio_base + growth * (1.0 - width_ratio_base)
        height_ratio = 0.84 + growth * 0.16
        max_logo_width = min(slot_width, int(slot_width * width_ratio))
        max_logo_height = min(
            logo_area_height, int(logo_area_height * height_ratio)
        )
        logos = [
            _load_logo(source, (max_logo_width, max_logo_height))
            for source in logo_sources
        ]
        for index, logo in enumerate(logos):
            if logo is None:
                continue
            slot_x = side_x + logo_padding + index * (slot_width + logo_gap)
            logo_x = slot_x + (slot_width - logo.width) // 2
            logo_y = logo_top + (logo_area_height - logo.height) // 2
            canvas.paste(logo, (logo_x, logo_y), logo)
    else:
        logo_area_height = base_logo_area_height
        placeholder_height = int(90 * scale)
        placeholder_width = int(side_width * 0.52)
        placeholder_x = side_x + (side_width - placeholder_width) // 2
        draw.rectangle(
            (placeholder_x, logo_top, placeholder_x + placeholder_width, logo_top + placeholder_height),
            fill="#f3f4f6",
            outline="#9ca3af",
            width=2,
        )
        placeholder_font = _font(max(14, int(18 * scale)), bold=True)
        _draw_centered_wrapped_text(
            draw,
            "LOGO INSTANSI",
            (placeholder_x, logo_top, placeholder_width, placeholder_height),
            placeholder_font,
            "#4b5563",
        )
    institution_top = logo_top + logo_area_height + int(10 * scale)

    _draw_centered_wrapped_text(
        draw,
        institution_name or "Nama Instansi",
        (side_x + 16, institution_top, side_width - 32, institution_text_height),
        institution_font,
        "#111827",
    )
    scale_y = institution_top + institution_block_height
    if template_name == PUBLICATION_TEMPLATE_RTRW:
        block_y = institution_top + institution_text_height + int(8 * scale)
        _draw_sidebar_rule(draw, side_x, side_width, block_y, scale)
        block_y = _draw_rtrw_boxed_text(
            draw, side_x, side_width, block_y, scale, rtrw_title_lines, size=19
        )
        block_y = _draw_rtrw_boxed_text(
            draw, side_x, side_width, block_y, scale, rtrw_type_lines, size=23
        )
        _draw_north_arrow(
            canvas,
            (
                side_x + side_width - int(84 * scale),
                block_y + int(10 * scale),
                int(44 * scale),
                int(70 * scale),
            ),
        )
        scale_y = block_y + int(62 * scale)
    _draw_scale_bar(
        draw,
        bounds,
        map_box[2],
        (side_x + int(32 * scale), scale_y, side_width - int(64 * scale), int(55 * scale)),
        scale=scale,
        output_dpi=output_dpi,
    )

    legend_y = scale_y + int(36 * scale)
    if template_name == PUBLICATION_TEMPLATE_RTRW:
        legend_y = _draw_rtrw_projection_block(
            draw, side_x, side_width, legend_y, scale
        ) + int(10 * scale)
    heading_font = _font(max(20, int(26 * scale)), bold=True)
    label_font = _font(max(15, int(18 * scale)))
    small_font = _font(max(13, int(15 * scale)))
    # Compact legends (see _should_compact_sidebar_legends) use smaller text.
    compact_label_font = _font(max(13, int(15 * scale)))
    compact_small_font = _font(max(11, int(13 * scale)))
    if template_name == PUBLICATION_TEMPLATE_BANDUNG:
        bandung_heading = _serif_font(max(18, int(23 * scale)), bold=True)
        bandung_subheading = _serif_font(
            max(13, int(16 * scale)),
            italic=True,
        )
        center_x = side_x + side_width // 2
        draw.text(
            (center_x, legend_y),
            "KETERANGAN SATUAN PETA",
            font=bandung_heading,
            fill="#111111",
            anchor="ma",
        )
        draw.text(
            (center_x, legend_y + max(22, int(25 * scale))),
            "DESCRIPTION OF MAP UNITS",
            font=bandung_subheading,
            fill="#333333",
            anchor="ma",
        )
        legend_y += max(50, int(55 * scale))
    else:
        draw.text(
            (side_x + int(24 * scale), legend_y),
            "KETERANGAN" if template_name == PUBLICATION_TEMPLATE_RTRW else "Legenda",
            font=heading_font,
            fill="#111827",
        )
        legend_y += int(38 * scale)
    if (
        show_basemap
        and legend_basemap_name == BASEMAP_SHORTBREAD
        and show_basemap_legend
    ):
        swatch_size = int(16 * scale)
        entries = [
            ("#eef1e8", "Daratan"),
            ("#b9ddea", "Perairan"),
        ]
        entry_width = (side_width - int(48 * scale)) / 2
        for index, (color, label) in enumerate(entries):
            entry_x = side_x + int(24 * scale) + index * entry_width
            draw.rectangle(
                (
                    entry_x,
                    legend_y,
                    entry_x + swatch_size,
                    legend_y + swatch_size,
                ),
                fill=color,
                outline="#59636e",
                width=1,
            )
            draw.text(
                (entry_x + swatch_size + int(7 * scale), legend_y - int(2 * scale)),
                label,
                font=small_font,
                fill="#111827",
            )
        legend_y += int(26 * scale)
        shortbread_line_entries: list[tuple[str, str]] = []
        if show_shortbread_roads:
            shortbread_line_entries.append(("#d99b72", "Jalan"))
        if show_shortbread_rivers:
            shortbread_line_entries.append(("#327fa3", "Sungai"))
        if shortbread_line_entries:
            entry_width = (side_width - int(48 * scale)) / 2
            for index, (color, label) in enumerate(shortbread_line_entries):
                entry_x = side_x + int(25 * scale) + index * entry_width
                draw.line(
                    (
                        entry_x,
                        legend_y + int(8 * scale),
                        entry_x + int(50 * scale),
                        legend_y + int(8 * scale),
                    ),
                    fill=color,
                    width=max(2, int(3 * scale)),
                )
                draw.text(
                    (entry_x + int(63 * scale), legend_y),
                    label,
                    font=label_font,
                    fill="#111827",
                )
            legend_y += int(26 * scale)
        if show_shortbread_topography:
            relief_x = side_x + int(25 * scale)
            relief_width = int(50 * scale)
            relief_height = int(14 * scale)
            for offset in range(relief_width):
                shade = int(245 - (offset / max(1, relief_width - 1)) * 125)
                draw.line(
                    (
                        relief_x + offset,
                        legend_y,
                        relief_x + offset,
                        legend_y + relief_height,
                    ),
                    fill=(shade, shade, shade),
                    width=1,
                )
            draw.rectangle(
                (
                    relief_x,
                    legend_y,
                    relief_x + relief_width,
                    legend_y + relief_height,
                ),
                outline="#59636e",
                width=1,
            )
            draw.text(
                (side_x + int(88 * scale), legend_y - int(3 * scale)),
                "Relief topografi",
                font=label_font,
                fill="#111827",
            )
            legend_y += int(26 * scale)
    tectonic_entries: list[str] = []
    if show_trenches and trench_geojson is not None:
        tectonic_entries.append("Tunjaman")
    if show_faults:
        tectonic_entries.extend(["Sesar Geser", "Sesar Naik", "Sesar Turun"])
    # In compact mode the tectonic/slab/province symbols and rows are shrunk to
    # reclaim the space the full-size magnitude circles need below.
    tectonic_symbol_scale = scale * 0.72 if compact_legends else scale
    tectonic_symbol_width = (58 if compact_legends else 80) * scale
    tectonic_label_x = side_x + int((100 if compact_legends else 120) * scale)
    tectonic_label_font = compact_label_font if compact_legends else label_font
    tectonic_row_height = int((20 if compact_legends else 25) * scale)
    tectonic_line_y = (7 if compact_legends else 9) * scale
    for tectonic_label in tectonic_entries:
        symbol_type = (
            "Sesar Normal" if tectonic_label == "Sesar Turun" else tectonic_label
        )
        _draw_tectonic_symbol(
            draw,
            (side_x + 25 * scale, legend_y),
            tectonic_symbol_width,
            symbol_type,
            tectonic_symbol_scale,
        )
        draw.text(
            (tectonic_label_x, legend_y),
            tectonic_label,
            font=tectonic_label_font,
            fill="#111827",
        )
        legend_y += tectonic_row_height
    if show_slabs and slab_geojson is not None:
        _draw_dashed_line(
            draw,
            [
                (side_x + 25 * scale, legend_y + tectonic_line_y),
                (side_x + 105 * scale, legend_y + tectonic_line_y),
            ],
            SLAB_CONTOUR_COLOR,
            max(2, int(2.4 * scale)),
            max(4, int(6 * scale)),
        )
        draw.text(
            (tectonic_label_x, legend_y),
            "Kontur slab (km)",
            font=tectonic_label_font,
            fill="#111827",
        )
        legend_y += tectonic_row_height

    if show_provinces:
        draw.line(
            (side_x + 25 * scale, legend_y + tectonic_line_y, side_x + 105 * scale, legend_y + tectonic_line_y),
            fill="#064e3b",
            width=max(2, int(3 * scale)),
        )
        draw.text((tectonic_label_x, legend_y), "Batas provinsi", font=tectonic_label_font, fill="#111827")
        legend_y += tectonic_row_height

    if show_stations:
        triangle_cx = side_x + int(64 * scale)
        triangle_r = 7 * scale
        triangle_cy = legend_y + tectonic_line_y
        draw.polygon(
            (
                (triangle_cx, triangle_cy - triangle_r),
                (triangle_cx - triangle_r * 0.9, triangle_cy + triangle_r * 0.72),
                (triangle_cx + triangle_r * 0.9, triangle_cy + triangle_r * 0.72),
            ),
            fill=SEISMIC_STATION_COLOR,
            outline=SEISMIC_STATION_BORDER,
        )
        draw.text((tectonic_label_x, legend_y), "Stasiun seismik", font=tectonic_label_font, fill="#111827")
        legend_y += tectonic_row_height

    if show_population_density and show_population_density_legend:
        legend_y = _draw_population_density_legend(
            draw,
            side_x,
            side_width,
            legend_y + int((5 if compact_legends else 8) * scale),
            scale,
            compact_label_font if compact_legends else label_font,
            compact_small_font if compact_legends else small_font,
            population_density_breaks,
            population_density_colors,
            compact=compact_legends,
        )

    if hazard_layer is not None:
        legend_y = _draw_class_legend(
            draw,
            side_x,
            side_width,
            legend_y + int((5 if compact_legends else 8) * scale),
            scale,
            compact_label_font if compact_legends else label_font,
            compact_small_font if compact_legends else small_font,
            hazard_layer.heading,
            hazard_layer.colors,
            hazard_layer.labels,
            compact=compact_legends,
        )

    if has_earthquakes:
        legend_y += int((5 if compact_legends else 8) * scale)
        magnitude_title_font = compact_label_font if compact_legends else label_font
        magnitude_value_font = compact_small_font if compact_legends else small_font
        draw.text((side_x + int(24 * scale), legend_y), "Magnitudo", font=magnitude_title_font, fill="#111827")
        legend_y += int((20 if compact_legends else 25) * scale)
        magnitude_labels = ["1", "2", "3", "4", "5", "6", "7", "8+"]
        magnitude_step = (side_width - int(54 * scale)) / len(magnitude_labels)
        # Row height/spacing stays integer; the drawn circle keeps the exact
        # fractional radius so it matches the plotted markers pixel-for-pixel.
        # The circles are never scaled down in compact mode - they are the size
        # key for the markers, so shrinking them would misreport marker sizes.
        # Compact mode still saves space through smaller fonts and tighter rows.
        maximum_radius = math.ceil(_publication_magnitude_radius(8.0, marker_scale))
        magnitude_center_y = legend_y + maximum_radius
        for index, label in enumerate(magnitude_labels):
            magnitude = float(index + 1)
            radius = _publication_magnitude_radius(magnitude, marker_scale)
            center_x = side_x + int(27 * scale) + magnitude_step * (index + 0.5)
            draw.ellipse(
                (center_x - radius, magnitude_center_y - radius, center_x + radius, magnitude_center_y + radius),
                fill="#e5e7eb",
                outline="#374151",
                width=1,
            )
            label_box = draw.textbbox((0, 0), label, font=magnitude_value_font)
            draw.text(
                (center_x - (label_box[2] - label_box[0]) / 2, magnitude_center_y + maximum_radius + int((3 if compact_legends else 4) * scale)),
                label,
                font=magnitude_value_font,
                fill="#111827",
            )
        legend_y = magnitude_center_y + maximum_radius + int((18 if compact_legends else 26) * scale)

        depth_title_font = compact_label_font if compact_legends else label_font
        depth_value_font = compact_small_font if compact_legends else small_font
        draw.text((side_x + int(24 * scale), legend_y), "Kedalaman (km)", font=depth_title_font, fill="#111827")
        legend_y += int((20 if compact_legends else 26) * scale)
        depth_entries = [
            ("#ed2939", "<= 50"),
            ("#f04b23", "> 50-100"),
            ("#f5a623", "> 100-250"),
            ("#087830", "> 250-600"),
            ("#2534d2", "> 600"),
        ]
        depth_column_width = (side_width - int(48 * scale)) / 2
        depth_row_height = int((20 if compact_legends else 27) * scale)
        depth_swatch_size = int((11 if compact_legends else 14) * scale)
        for index, (color, label) in enumerate(depth_entries):
            column = index % 2
            row = index // 2
            entry_x = side_x + int(24 * scale) + column * depth_column_width
            entry_y = legend_y + row * depth_row_height
            swatch_size = depth_swatch_size
            draw.rectangle(
                (entry_x, entry_y, entry_x + swatch_size, entry_y + swatch_size),
                fill=color,
                outline="#374151",
                width=1,
            )
            draw.text(
                (entry_x + swatch_size + int((5 if compact_legends else 7) * scale), entry_y - int(2 * scale)),
                label,
                font=depth_value_font,
                fill="#111827",
            )
        legend_y += int((58 if compact_legends else 76) * scale)

    source_lines = _publication_source_lines(
        source_text,
        attribution,
        show_basemap=show_basemap,
        show_earthquakes=has_earthquakes,
        show_faults=show_faults,
        show_trenches=show_trenches,
        show_slabs=show_slabs,
        show_population_density=show_population_density,
        show_provinces=show_provinces,
        show_inset=show_inset,
        fault_geojson=fault_geojson,
        trench_geojson=trench_geojson,
        slab_geojson=slab_geojson,
        population_density_mode="district" if district_names else "province",
        hazard_source=hazard_layer.source if hazard_layer else None,
    )
    # The block has to grow with the credits it carries; a fixed height silently
    # clips the last source when many layers are credited. Counting source
    # lines is not enough either - most of them wrap to two rendered lines - so
    # the wrap is measured against the real column width.
    source_font = _font(max(12, int(13 * scale)))
    source_text_width = max(1, side_width - 32)
    source_line_height = (
        draw.textbbox((0, 0), "Ag", font=source_font)[3] + int(4 * scale)
    )
    rendered_source_lines = sum(
        max(1, math.ceil(draw.textlength(line, font=source_font) / source_text_width))
        for line in source_lines
    )
    source_area_height = max(
        int(150 * scale),
        int(46 * scale) + rendered_source_lines * source_line_height,
    )
    source_y = side_y + side_height - source_area_height

    inset_top = legend_y + int(6 * scale)
    if show_inset:
        _draw_locator_inset(
            canvas,
            draw,
            province_geojson,
            selection_bounds or bounds,
            sidebar_box,
            inset_top,
            source_y,
            scale,
            label_font,
            # Kotak seleksi ditandai di tengah wilayah yang lebih luas agar
            # lokasinya tetap mudah dikenali.
            **(
                {"expand": 4.0, "min_span": 8.0}
                if selection_bounds is not None
                else {}
            ),
        )

    draw.line(
        (side_x, source_y - int(8 * scale), side_x + side_width, source_y - int(8 * scale)),
        fill="#111827",
        width=1,
    )
    source = "\n".join(source_lines)
    draw.text(
        (side_x + 16, source_y),
        "Sumber data",
        font=label_font,
        fill="#111827",
    )
    _draw_wrapped_text(
        draw,
        source,
        (
            side_x + 16,
            source_y + int(25 * scale),
            side_width - 32,
            source_area_height - int(25 * scale),
        ),
        source_font,
        "#374151",
    )
    png_buffer = BytesIO()
    canvas.save(
        png_buffer,
        format="PNG",
        optimize=True,
        dpi=(float(output_dpi), float(output_dpi)),
    )
    pdf_buffer = BytesIO()
    canvas.save(
        pdf_buffer,
        format="PDF",
        resolution=output_dpi,
    )
    return png_buffer.getvalue(), pdf_buffer.getvalue()
