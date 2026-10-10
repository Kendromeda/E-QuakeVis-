from __future__ import annotations

import hashlib
from functools import lru_cache
from html import escape
from io import BytesIO
import json
import math
import base64
import re
from typing import Any, Collection

import folium
import numpy as np
import pandas as pd
from branca.element import MacroElement, Template
from folium.plugins import PolyLineTextPath, VectorGridProtobuf
from PIL import Image, ImageColor, ImageDraw

from utils.volcano_data import (
    filter_volcano_features,
    get_volcano_feature_name,
    iter_volcano_points,
)
from utils.geology_layer import (
    GEOLOGY_LAYER_NAME,
    GEOLOGY_MAPSERVER_URL,
    add_dynamic_geology_layer,
    build_geology_region_filter,
)
from utils.geology_catalog import GEOLOGY_STYLE_OFFICIAL


DEFAULT_CENTER = (-6.2088, 106.8456)
DEFAULT_ZOOM = 9
OPENTOPO_ATTRIBUTION = (
    "Map data: &copy; OpenStreetMap contributors, SRTM | "
    "Map style: &copy; OpenTopoMap (CC-BY-SA)"
)
BASEMAP_OPENTOPO = "OpenTopoMap"
BASEMAP_RELIEF = "Topografi Berwarna"
BASEMAP_CYCLOSM = "CyclOSM"
BASEMAP_HUMANITARIAN = "Humanitarian"
BASEMAP_SHORTBREAD = "Shortbread"
BASEMAP_OPTIONS = (
    BASEMAP_OPENTOPO,
    BASEMAP_RELIEF,
    BASEMAP_CYCLOSM,
    BASEMAP_HUMANITARIAN,
    BASEMAP_SHORTBREAD,
)
RELIEF_ATTRIBUTION = (
    "Topografi: Esri World Topographic Map | Sources: Esri, HERE, Garmin, "
    "Intermap, increment P Corp., GEBCO, USGS, FAO, NPS, NRCAN, GeoBase, "
    "IGN, Kadaster NL, Ordnance Survey, Esri Japan, METI, Esri China "
    "(Hong Kong), swisstopo, MapmyIndia, and the GIS User Community"
)
CYCLOSM_ATTRIBUTION = (
    "Map style: &copy; CyclOSM | Map data: &copy; OpenStreetMap contributors"
)
HUMANITARIAN_ATTRIBUTION = (
    "Tiles &copy; HOT, hosted by OpenStreetMap France | "
    "Data &copy; OpenStreetMap contributors"
)
SHORTBREAD_ATTRIBUTION = (
    "VersaTiles Shortbread | Data &copy; OpenStreetMap contributors | "
    "Landcover &copy; ESA WorldCover 2021"
)
HILLSHADE_ATTRIBUTION = (
    "Relief: Esri World Hillshade | Sources: Esri, Vantor, Airbus, USGS, NASA, "
    "and the GIS user community"
)
POPULATION_DENSITY_LAYER_NAME = "Kriging Kepadatan Penduduk (BPS)"
POPULATION_DENSITY_DISTRICT_LAYER_NAME = (
    "Kepadatan Penduduk per Kecamatan (BPS)"
)
POPULATION_DENSITY_COLORS = (
    "#ecfeff",
    "#cffafe",
    "#a5f3fc",
    "#67e8f9",
    "#22d3ee",
    "#06b6d4",
    "#0284c7",
    "#1d4ed8",
    "#172554",
)
POPULATION_DENSITY_DISTRICT_COLORS = (
    "#ecfeff",
    "#a5f3fc",
    "#22d3ee",
    "#06b6d4",
    "#0284c7",
    "#1d4ed8",
    "#172554",
)
POPULATION_DENSITY_BREAKS = (
    0,
    250,
    500,
    1_000,
    2_000,
    4_000,
    8_000,
    16_000,
    32_000,
    50_000,
)


def fit_map_bounds(
    earthquake_map: folium.Map,
    bounds: list[list[float]],
    padding: tuple[int, int] = (0, 0),
) -> None:
    """Fit bounds synchronously so tile layers only request the final zoom."""
    fit_bounds = MacroElement()
    fit_bounds._name = "FitBoundsImmediate"
    fit_bounds.bounds = bounds
    fit_bounds.options = {"padding": list(padding), "animate": False}
    fit_bounds._template = Template(
        """
        {% macro script(this, kwargs) %}
        {{ this._parent.get_name() }}.fitBounds(
            {{ this.bounds | tojson }},
            {{ this.options | tojson }}
        );
        {% endmacro %}
        """
    )
    fit_bounds.add_to(earthquake_map)


def _add_collapsible_legend_styles(earthquake_map: folium.Map) -> None:
    """Style compact native-details controls shared by all map legends."""
    earthquake_map.get_root().header.add_child(
        folium.Element(
            """
            <style>
            .map-legend{position:fixed;z-index:9999;background:rgba(255,255,255,.96);
              border:1px solid #9ca3af;border-radius:6px;color:#000;
              font:12px/1.4 Arial,sans-serif;box-shadow:0 1px 5px rgba(0,0,0,.2);}
            .map-legend summary{cursor:pointer;list-style:none;font-weight:700;
              padding:7px 10px;white-space:nowrap;color:#000;user-select:none;}
            .map-legend summary::-webkit-details-marker{display:none;}
            .map-legend summary::after{content:'▼';font-size:9px;margin-left:9px;}
            .map-legend[open] summary::after{content:'▲';}
            .map-legend-body{padding:2px 10px 9px;color:#000;max-height:310px;overflow:auto;}
            .map-legend-earthquake{left:10px;bottom:66px;}
            .map-legend-tectonic{right:10px;bottom:66px;}
            .map-legend-density{right:10px;top:54px;}
            .map-legend-hazard{right:10px;top:54px;max-width:300px;}
            .map-legend-geology{left:10px;bottom:110px;max-width:440px;}
            .map-legend-subtitle{font-weight:600;margin:2px 0 6px;}
            .geology-legend-grid{display:grid;grid-template-columns:1fr 1fr;
              gap:3px 12px;margin:4px 0 7px;}
            .geology-legend-row{display:flex;align-items:center;white-space:nowrap;
              font-size:10px;}
            .geology-legend-row span{width:13px;height:11px;border:1px solid #374151;
              margin-right:5px;box-sizing:border-box;flex:0 0 auto;}
            .geology-legend-note{border-top:1px solid #d1d5db;border-bottom:1px solid #d1d5db;
              margin:5px 0;padding:5px 0;font-size:10px;line-height:1.35;}
            .geology-formation-index{margin-top:6px;border-top:1px solid #d1d5db;padding-top:4px;}
            .geology-formation-row{display:flex;gap:6px;padding:5px 0;border-bottom:1px solid #e5e7eb;}
            .geology-formation-row>span{width:14px;height:14px;border:1px solid #374151;flex:0 0 auto;}
            .geology-formation-row small{display:block;max-width:360px;line-height:1.3;color:#374151;}
            .density-legend-grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 10px;}
            .density-legend-row{display:flex;align-items:center;white-space:nowrap;}
            .density-legend-row span{width:14px;height:12px;border:1px solid #111827;
              margin-right:5px;box-sizing:border-box;}
            .density-legend-source{border-top:1px solid #d1d5db;margin-top:7px;
              padding-top:6px;font-size:10px;line-height:1.35;color:#000;}
            .density-legend-source a{color:#000;text-decoration:underline;}
            </style>
            """
        )
    )


def _optional_record_value(row: Any, field: str) -> Any:
    value = getattr(row, field, None)
    if value is None or not pd.notna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    return value


def get_earthquake_event_id(row: Any) -> str:
    """Return a stable identifier derived from an earthquake's core fields."""
    event_time = _optional_record_value(row, "waktu")
    if event_time is None:
        event_time = (
            f"{_optional_record_value(row, 'tanggal') or ''}T"
            f"{_optional_record_value(row, 'jam') or ''}"
        )

    def canonical_number(field: str) -> str:
        value = _optional_record_value(row, field)
        if value is None:
            return ""
        return format(float(value), ".12g")

    payload = "|".join(
        [
            str(event_time),
            canonical_number("lintang"),
            canonical_number("bujur"),
            canonical_number("kedalaman"),
            canonical_number("magnitudo"),
        ]
    )
    return f"eq_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def filter_hidden_earthquakes(
    df: pd.DataFrame,
    hidden_event_ids: Collection[str] | None,
) -> pd.DataFrame:
    """Exclude manually hidden earthquakes without mutating the source frame."""
    hidden_ids = set(hidden_event_ids or [])
    if df.empty or not hidden_ids:
        return df.copy()
    visible_mask = [
        get_earthquake_event_id(row) not in hidden_ids
        for row in df.itertuples(index=False)
    ]
    return df.loc[visible_mask].copy()


def add_population_density_layer(
    earthquake_map: folium.Map,
    population_density_geojson: dict[str, Any],
    *,
    province_geojson: dict[str, Any] | None = None,
    show_legend: bool = True,
    mode: str = "province",
) -> None:
    """Add provincial ordinary kriging or district subdistrict choropleth density."""
    if not population_density_geojson.get("features"):
        return

    is_district_mode = mode == "district"
    colors = (
        POPULATION_DENSITY_DISTRICT_COLORS
        if is_district_mode
        else POPULATION_DENSITY_COLORS
    )
    density_breaks = get_population_density_breaks(
        population_density_geojson,
        class_count=len(colors),
    )
    density_layer = folium.FeatureGroup(
        name=(
            POPULATION_DENSITY_DISTRICT_LAYER_NAME
            if is_district_mode
            else POPULATION_DENSITY_LAYER_NAME
        ),
        show=True,
    )
    if is_district_mode:
        folium.GeoJson(
            population_density_geojson,
            name=POPULATION_DENSITY_DISTRICT_LAYER_NAME,
            pane="populationDensityPane",
            smooth_factor=1.4,
            style_function=lambda feature: {
                "pane": "populationDensityPane",
                "color": "#111827",
                "weight": 0.75,
                "opacity": 0.78,
                "fillColor": get_population_density_color(
                    float(
                        (feature.get("properties") or {}).get(
                            "kepadatan_penduduk_per_km2", 0
                        )
                    ),
                    density_breaks,
                    colors,
                ),
                "fillOpacity": 0.86,
            },
            highlight_function=lambda _feature: {
                "color": "#000000",
                "weight": 2.0,
                "fillOpacity": 0.94,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=[
                    "kecamatan",
                    "kabupaten_kota",
                    "kepadatan_penduduk_per_km2",
                    "tahun",
                ],
                aliases=[
                    "Kecamatan",
                    "Kabupaten/Kota",
                    "Kepadatan (jiwa/km²)",
                    "Tahun",
                ],
                localize=True,
                sticky=False,
            ),
        ).add_to(density_layer)
    else:
        for surface in build_population_density_surfaces(
            population_density_geojson,
            province_geojson,
            density_breaks=density_breaks,
        ):
            folium.raster_layers.ImageOverlay(
                image=surface["data_url"],
                bounds=surface["bounds"],
                opacity=1.0,
                interactive=False,
                cross_origin=False,
                pane="populationDensityPane",
                zindex=1,
            ).add_to(density_layer)
    density_layer.add_to(earthquake_map)
    if show_legend:
        source_years = sorted(
            {
                int(year)
                for feature in population_density_geojson.get("features", [])
                if (year := (feature.get("properties") or {}).get("tahun"))
                is not None
            }
        )
        _add_population_density_legend(
            earthquake_map,
            density_breaks,
            colors,
            district_mode=is_district_mode,
            source_years=source_years,
        )


def _add_population_density_legend(
    earthquake_map: folium.Map,
    breaks: Collection[float],
    colors: Collection[str],
    *,
    district_mode: bool,
    source_years: Collection[int] = (),
) -> None:
    labels = get_population_density_legend_labels(breaks, colors)
    rows = "".join(
        '<div class="density-legend-row">'
        f'<span style="background:{color}"></span>{escape(label)}</div>'
        for color, label in zip(colors, labels)
    )
    mode_label = "Kecamatan" if district_mode else "Kriging Provinsi"
    years = sorted(set(source_years))
    if not years:
        year_label = ""
    elif len(years) == 1:
        year_label = f", data {years[0]}"
    else:
        year_label = f", data {years[0]}–{years[-1]}"
    legend_html = f"""
    <style>.leaflet-image-layer{{image-rendering:auto!important;}}</style>
    <details class="map-legend map-legend-density">
      <summary>Legenda Kepadatan</summary>
      <div class="map-legend-body">
        <div class="map-legend-subtitle">{mode_label} (jiwa/km²)</div>
        <div class="density-legend-grid">{rows}</div>
        <div class="density-legend-source">Sumber data: <a
          href="https://www.bps.go.id/" target="_blank" rel="noopener noreferrer"
          >Badan Pusat Statistik (BPS)</a>{year_label}; tabel dan publikasi
          kabupaten/kota.</div>
      </div>
    </details>
    """
    earthquake_map.get_root().html.add_child(folium.Element(legend_html))


HAZARD_LAYER_NAME = "Bahaya Gempa (PSHA)"


def zoom_to_fit(
    bounds: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
    max_zoom: int = 12,
) -> int:
    """Largest Web Mercator zoom showing ``(west, south, east, north)`` whole.

    Computed up front because ``fitBounds`` on a map rendered inside a hidden
    Streamlit tab sees a zero-sized container and jumps to the maximum zoom.
    """
    west, south, east, north = bounds

    def mercator_y(lat: float) -> float:
        return math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

    lon_fraction = max(east - west, 1e-6) / 360
    lat_fraction = max(mercator_y(north) - mercator_y(south), 1e-6) / (2 * math.pi)
    zoom = min(math.log2(width_px / 256 / lon_fraction),
               math.log2(height_px / 256 / lat_fraction))
    return max(1, min(max_zoom, math.floor(zoom)))


def hazard_image_data_url(
    classes: np.ndarray,
    bounds: tuple[float, float, float, float],
    colors: Collection[str],
    *,
    pixels_per_cell: int = 4,
    alpha: int = 215,
) -> str:
    """PNG of a classified north-up lon/lat grid, resampled to Web Mercator rows.

    Leaflet stretches an image overlay linearly in Mercator space, so rows are
    sampled at equal Mercator steps to keep every cell at its true latitude.
    Cells with class -1 (no site) are transparent.
    """
    _, south, _, north = bounds
    row_count, col_count = classes.shape
    height, width = row_count * pixels_per_cell, col_count * pixels_per_cell

    def mercator(lat: np.ndarray) -> np.ndarray:
        return np.log(np.tan(np.pi / 4 + np.radians(lat) / 2))

    y = np.linspace(mercator(np.array(north)), mercator(np.array(south)), height + 1)
    row_lat = np.degrees(2 * np.arctan(np.exp((y[:-1] + y[1:]) / 2)) - np.pi / 2)
    rows = np.clip(((north - row_lat) / (north - south) * row_count).astype(int), 0, row_count - 1)
    cols = np.arange(width) // pixels_per_cell
    sampled = classes[rows][:, cols]
    palette = np.asarray([ImageColor.getrgb(color) for color in colors], dtype=np.uint8)
    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    valid = sampled >= 0
    rgba[valid, :3] = palette[sampled[valid]]
    rgba[valid, 3] = alpha
    buffer = BytesIO()
    Image.fromarray(rgba).save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def add_hazard_layer(
    earthquake_map: folium.Map,
    classes: np.ndarray,
    bounds: tuple[float, float, float, float],
    colors: Collection[str],
    legend_labels: Collection[str],
    *,
    title: str,
    subtitle: str,
    source_html: str,
) -> None:
    """Add a classified PSHA hazard raster below the tectonic lines, with legend."""
    west, south, east, north = bounds
    hazard_layer = folium.FeatureGroup(name=HAZARD_LAYER_NAME, show=True)
    folium.raster_layers.ImageOverlay(
        image=hazard_image_data_url(classes, bounds, colors),
        bounds=[[south, west], [north, east]],
        opacity=1.0,
        interactive=False,
        cross_origin=False,
        pane="hazardPane",
        zindex=1,
    ).add_to(hazard_layer)
    hazard_layer.add_to(earthquake_map)
    rows = "".join(
        '<div class="density-legend-row">'
        f'<span style="background:{color}"></span>{escape(label)}</div>'
        for color, label in zip(colors, legend_labels)
    )
    legend_html = f"""
    <details class="map-legend map-legend-hazard" open>
      <summary>{escape(title)}</summary>
      <div class="map-legend-body">
        <div class="map-legend-subtitle">{escape(subtitle)}</div>
        <div class="density-legend-grid">{rows}</div>
        <div class="density-legend-source">{source_html}</div>
      </div>
    </details>
    """
    earthquake_map.get_root().html.add_child(folium.Element(legend_html))


def _iter_polygon_rings(
    geometry: dict[str, Any],
) -> list[tuple[list[list[float]], list[list[list[float]]]]]:
    """Return polygon outer rings paired with their holes."""
    coordinates = geometry.get("coordinates", [])
    if geometry.get("type") == "Polygon" and coordinates:
        return [(coordinates[0], coordinates[1:])]
    if geometry.get("type") == "MultiPolygon":
        return [
            (polygon[0], polygon[1:])
            for polygon in coordinates
            if polygon
        ]
    return []


def _geometry_representative_point(
    geometry: dict[str, Any],
) -> tuple[float, float] | None:
    polygons = _iter_polygon_rings(geometry)
    if not polygons:
        return None
    outer, _holes = max(
        polygons,
        key=lambda item: abs(
            sum(
                float(start[0]) * float(end[1])
                - float(end[0]) * float(start[1])
                for start, end in zip(item[0], item[0][1:])
            )
        ),
    )
    latitude, longitude = _ring_centroid(outer)
    return longitude, latitude


def _geometry_bounds(geometry: dict[str, Any]) -> tuple[float, float, float, float] | None:
    points = [
        point
        for outer, holes in _iter_polygon_rings(geometry)
        for ring in [outer, *holes]
        for point in ring
        if len(point) >= 2
    ]
    if not points:
        return None
    return (
        min(float(point[0]) for point in points),
        min(float(point[1]) for point in points),
        max(float(point[0]) for point in points),
        max(float(point[1]) for point in points),
    )


def _surface_mask(
    geometries: list[dict[str, Any]],
    bounds: tuple[float, float, float, float],
    size: tuple[int, int],
) -> Image.Image:
    min_lon, min_lat, max_lon, max_lat = bounds
    width, height = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)

    def project(point: list[float]) -> tuple[float, float]:
        x = (float(point[0]) - min_lon) / max(max_lon - min_lon, 1e-12) * (width - 1)
        y = (max_lat - float(point[1])) / max(max_lat - min_lat, 1e-12) * (height - 1)
        return x, y

    for geometry in geometries:
        for outer, holes in _iter_polygon_rings(geometry):
            draw.polygon([project(point) for point in outer], fill=255)
            for hole in holes:
                draw.polygon([project(point) for point in hole], fill=0)
    return mask


def _density_surface_image(
    samples: list[tuple[float, float, float]],
    mask_geometries: list[dict[str, Any]],
    bounds: tuple[float, float, float, float],
    density_breaks: Collection[float],
    max_dimension: int = 200,
) -> Image.Image:
    min_lon, min_lat, max_lon, max_lat = bounds
    lon_span = max(max_lon - min_lon, 1e-6)
    lat_span = max(max_lat - min_lat, 1e-6)
    aspect_lon = lon_span * max(0.2, math.cos(math.radians((min_lat + max_lat) / 2)))
    largest_span = max(aspect_lon, lat_span)
    width = max(64, round(max_dimension * aspect_lon / largest_span))
    height = max(64, round(max_dimension * lat_span / largest_span))
    longitudes = np.linspace(min_lon, max_lon, width, dtype=np.float64)
    latitudes = np.linspace(max_lat, min_lat, height, dtype=np.float64)
    interpolated = _ordinary_kriging_grid(
        tuple(
            (round(longitude, 8), round(latitude, 8), round(density, 6))
            for longitude, latitude, density in samples
        ),
        tuple(round(value, 8) for value in bounds),
        width,
        height,
    )
    break_values = np.asarray(list(density_breaks), dtype=float)
    color_indexes = np.searchsorted(
        break_values[1:], interpolated, side="left"
    ).clip(0, len(POPULATION_DENSITY_COLORS) - 1)
    palette = np.asarray(
        [ImageColor.getrgb(color) for color in POPULATION_DENSITY_COLORS],
        dtype=np.uint8,
    )
    rgba = np.empty((height, width, 4), dtype=np.uint8)
    rgba[:, :, :3] = palette[color_indexes]
    rgba[:, :, 3] = 232
    image = Image.fromarray(rgba)
    image.putalpha(_surface_mask(mask_geometries, bounds, (width, height)))
    alpha = image.getchannel("A").point(lambda value: round(value * 232 / 255))
    image.putalpha(alpha)
    return image


@lru_cache(maxsize=16)
def _ordinary_kriging_grid(
    samples: tuple[tuple[float, float, float], ...],
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
) -> np.ndarray:
    """Interpolate a grid with lightweight ordinary kriging.

    An exponential covariance model and a small nugget keep the system stable.
    The solved grid is cached because Streamlit rebuilds the same map frequently.
    """
    if not samples:
        return np.zeros((height, width), dtype=np.float64)

    # Average coincident samples before building the covariance system.
    grouped: dict[tuple[float, float], list[float]] = {}
    for longitude, latitude, density in samples:
        grouped.setdefault((longitude, latitude), []).append(density)
    sample_array = np.asarray(
        [
            (longitude, latitude, float(np.mean(densities)))
            for (longitude, latitude), densities in sorted(grouped.items())
        ],
        dtype=np.float64,
    )
    values = sample_array[:, 2]
    if len(sample_array) == 1 or np.allclose(values, values[0]):
        result = np.full((height, width), float(values[0]), dtype=np.float64)
        result.setflags(write=False)
        return result

    min_lon, min_lat, max_lon, max_lat = bounds
    center_lon = (min_lon + max_lon) / 2.0
    center_lat = (min_lat + max_lat) / 2.0
    km_per_lon_degree = 111.32 * max(
        0.2,
        math.cos(math.radians(center_lat)),
    )
    km_per_lat_degree = 110.574

    sample_x = (sample_array[:, 0] - center_lon) * km_per_lon_degree
    sample_y = (sample_array[:, 1] - center_lat) * km_per_lat_degree
    pairwise_distance = np.hypot(
        sample_x[:, None] - sample_x[None, :],
        sample_y[:, None] - sample_y[None, :],
    )
    positive_distances = pairwise_distance[
        np.triu_indices(len(sample_array), k=1)
    ]
    positive_distances = positive_distances[positive_distances > 1e-9]
    if positive_distances.size:
        practical_range_km = max(
            1.0,
            float(np.quantile(positive_distances, 0.65)),
            float(positive_distances.max()) * 0.4,
        )
    else:
        practical_range_km = 1.0

    covariance = np.exp(-3.0 * pairwise_distance / practical_range_km)
    # A 2% nugget handles very close centroids without visually flattening the field.
    covariance.flat[:: len(sample_array) + 1] += 0.02
    system = np.empty((len(sample_array) + 1, len(sample_array) + 1))
    system[:-1, :-1] = covariance
    system[:-1, -1] = 1.0
    system[-1, :-1] = 1.0
    system[-1, -1] = 0.0
    target = np.append(values, 0.0)
    try:
        coefficients = np.linalg.solve(system, target)
    except np.linalg.LinAlgError:
        coefficients = np.linalg.pinv(system, rcond=1e-10) @ target

    grid_longitudes = np.linspace(min_lon, max_lon, width, dtype=np.float64)
    grid_latitudes = np.linspace(max_lat, min_lat, height, dtype=np.float64)
    grid_lon, grid_lat = np.meshgrid(grid_longitudes, grid_latitudes)
    grid_x = ((grid_lon.ravel() - center_lon) * km_per_lon_degree)
    grid_y = ((grid_lat.ravel() - center_lat) * km_per_lat_degree)
    predicted = np.empty(grid_x.size, dtype=np.float64)
    chunk_size = 2048
    for start in range(0, grid_x.size, chunk_size):
        stop = min(start + chunk_size, grid_x.size)
        distances = np.hypot(
            sample_x[:, None] - grid_x[None, start:stop],
            sample_y[:, None] - grid_y[None, start:stop],
        )
        grid_covariance = np.exp(-3.0 * distances / practical_range_km)
        predicted[start:stop] = (
            coefficients[:-1] @ grid_covariance + coefficients[-1]
        )

    result = np.clip(predicted, values.min(), values.max()).reshape(height, width)
    result.setflags(write=False)
    return result


def build_population_density_surfaces(
    population_density_geojson: dict[str, Any] | None,
    province_geojson: dict[str, Any] | None = None,
    *,
    density_breaks: Collection[float] | None = None,
) -> list[dict[str, Any]]:
    """Create one ordinary-kriging, province-clipped raster per province."""
    features = (population_density_geojson or {}).get("features", [])
    if not features:
        return []
    breaks = tuple(
        density_breaks or get_population_density_breaks(population_density_geojson)
    )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        province = str((feature.get("properties") or {}).get("provinsi") or "").strip()
        if province:
            grouped.setdefault(province, []).append(feature)
    province_features = {
        str((feature.get("properties") or {}).get("PROVINSI") or "").strip(): feature
        for feature in (province_geojson or {}).get("features", [])
    }
    surfaces: list[dict[str, Any]] = []
    for province, province_density_features in sorted(grouped.items()):
        samples = []
        for feature in province_density_features:
            point = _geometry_representative_point(feature.get("geometry") or {})
            if point is None:
                continue
            samples.append(
                (
                    point[0],
                    point[1],
                    float(
                        (feature.get("properties") or {}).get(
                            "kepadatan_penduduk_per_km2", 0
                        )
                    ),
                )
            )
        if not samples:
            continue
        province_feature = province_features.get(province)
        mask_geometries = (
            [province_feature.get("geometry") or {}]
            if province_feature is not None
            else [feature.get("geometry") or {} for feature in province_density_features]
        )
        geometry_bounds = [
            bounds
            for geometry in mask_geometries
            if (bounds := _geometry_bounds(geometry)) is not None
        ]
        if not geometry_bounds:
            continue
        bounds = (
            min(value[0] for value in geometry_bounds),
            min(value[1] for value in geometry_bounds),
            max(value[2] for value in geometry_bounds),
            max(value[3] for value in geometry_bounds),
        )
        image = _density_surface_image(samples, mask_geometries, bounds, breaks)
        buffer = BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        min_lon, min_lat, max_lon, max_lat = bounds
        surfaces.append(
            {
                "province": province,
                "bounds": [[min_lat, min_lon], [max_lat, max_lon]],
                "image": image,
                "data_url": "data:image/png;base64,"
                + base64.b64encode(buffer.getvalue()).decode("ascii"),
                "sample_count": len(samples),
                "interpolation_method": "ordinary_kriging",
            }
        )
    return surfaces


def _add_thematic_panes(earthquake_map: folium.Map) -> None:
    """Give thematic overlays stable, explicit stacking levels."""
    pane_setup = MacroElement()
    pane_setup._name = "ThematicPanes"
    pane_setup._template = Template(
        """
        {% macro script(this, kwargs) %}
        var geologyPane = {{ this._parent.get_name() }}.getPane('geologyPane')
            || {{ this._parent.get_name() }}.createPane('geologyPane');
        geologyPane.style.zIndex = 340;
        geologyPane.style.pointerEvents = 'none';
        var hazardPane = {{ this._parent.get_name() }}.getPane('hazardPane')
            || {{ this._parent.get_name() }}.createPane('hazardPane');
        hazardPane.style.zIndex = 345;
        hazardPane.style.pointerEvents = 'none';
        var populationDensityPane = {{ this._parent.get_name() }}.getPane('populationDensityPane')
            || {{ this._parent.get_name() }}.createPane('populationDensityPane');
        populationDensityPane.style.zIndex = 350;
        populationDensityPane.style.pointerEvents = 'none';
        var districtFocusPane = {{ this._parent.get_name() }}.getPane('districtFocusPane')
            || {{ this._parent.get_name() }}.createPane('districtFocusPane');
        districtFocusPane.style.zIndex = 620;
        districtFocusPane.style.pointerEvents = 'none';
        var districtLabelPane = {{ this._parent.get_name() }}.getPane('districtLabelPane')
            || {{ this._parent.get_name() }}.createPane('districtLabelPane');
        districtLabelPane.style.zIndex = 630;
        districtLabelPane.style.pointerEvents = 'none';
        var earthquakePane = {{ this._parent.get_name() }}.getPane('earthquakePane')
            || {{ this._parent.get_name() }}.createPane('earthquakePane');
        earthquakePane.style.zIndex = 650;
        earthquakePane.style.pointerEvents = 'none';
        var earthquakePopupPane = {{ this._parent.get_name() }}.getPane('earthquakePopupPane')
            || {{ this._parent.get_name() }}.createPane(
                'earthquakePopupPane',
                {{ this._parent.get_name() }}.getContainer()
            );
        earthquakePopupPane.style.zIndex = 750;
        {% endmacro %}
        """
    )
    pane_setup.add_to(earthquake_map)


def get_population_density_breaks(
    population_density_geojson: dict[str, Any] | None,
    *,
    class_count: int | None = None,
) -> tuple[float, ...]:
    """Return quantile classes normalized to the active province or district."""
    class_count = class_count or len(POPULATION_DENSITY_COLORS)
    if class_count < 2:
        raise ValueError("Jumlah kelas kepadatan minimal dua.")
    values = [
        float(value)
        for feature in (population_density_geojson or {}).get("features", [])
        if math.isfinite(
            value := float(
                (feature.get("properties") or {}).get(
                    "kepadatan_penduduk_per_km2",
                    float("nan"),
                )
            )
        )
    ]
    if not values:
        if class_count == len(POPULATION_DENSITY_COLORS):
            return tuple(float(value) for value in POPULATION_DENSITY_BREAKS)
        return tuple(float(value) for value in np.linspace(0, 50_000, class_count + 1))

    minimum = min(values)
    maximum = max(values)
    if maximum <= minimum:
        padding = max(1.0, abs(minimum) * 0.05)
        minimum = max(0.0, minimum - padding)
        maximum += padding
    quantiles = np.quantile(
        np.asarray(values, dtype=float),
        np.linspace(0.0, 1.0, class_count + 1),
    )
    if any(
        float(current) <= float(previous)
        for previous, current in zip(quantiles, quantiles[1:])
    ):
        quantiles = np.linspace(minimum, maximum, class_count + 1)
    return tuple(float(value) for value in quantiles)


def get_population_density_legend_labels(
    breaks: Collection[float],
    colors: Collection[str] = POPULATION_DENSITY_COLORS,
) -> tuple[str, ...]:
    """Format adaptive density class ranges for the publication legend."""
    values = list(breaks)
    color_values = list(colors)
    if len(values) != len(color_values) + 1:
        raise ValueError("Jumlah ambang harus satu lebih banyak dari jumlah warna.")

    def format_value(value: float) -> str:
        return f"{value:,.0f}".replace(",", ".")

    return tuple(
        f"{format_value(values[index])}–{format_value(values[index + 1])}"
        for index in range(len(color_values))
    )


def get_population_density_color(
    value: float,
    breaks: Collection[float] = POPULATION_DENSITY_BREAKS,
    colors: Collection[str] = POPULATION_DENSITY_COLORS,
) -> str:
    """Return the sequential-blue color for a value within the active scope."""
    break_values = list(breaks)
    color_values = list(colors)
    if len(break_values) != len(color_values) + 1:
        raise ValueError("Jumlah ambang harus satu lebih banyak dari jumlah warna.")
    for upper_bound, color in zip(
        break_values[1:],
        color_values,
    ):
        if value <= upper_bound:
            return color
    return color_values[-1]


def _earthquake_canvas_records(
    df: pd.DataFrame,
    marker_scale: float,
) -> list[list[Any]]:
    records: list[list[Any]] = []
    for row in df.itertuples(index=False):
        depth = float(row.kedalaman)
        magnitude = float(row.magnitudo)
        radius = get_scaled_magnitude_radius(magnitude, marker_scale)
        if depth <= 50:
            depth_class = 0
        elif depth <= 100:
            depth_class = 1
        elif depth <= 250:
            depth_class = 2
        elif depth <= 600:
            depth_class = 3
        else:
            depth_class = 4
        records.append(
            [
                round(float(row.lintang), 6),
                round(float(row.bujur), 6),
                radius,
                depth_class,
                str(row.tanggal),
                str(row.jam),
                magnitude,
                depth,
                _optional_record_value(row, "sesar"),
                _optional_record_value(row, "jarak_sesar_km"),
                _optional_record_value(row, "provinsi"),
                _optional_record_value(row, "kabupaten_kota"),
                _optional_record_value(row, "jenis_lokasi"),
                _optional_record_value(row, "jarak_darat_km"),
                _optional_record_value(row, "keterangan_lokasi"),
                _optional_record_value(row, "dirasakan"),
                get_earthquake_event_id(row),
            ]
        )
    return records


def build_earthquake_feature_group(
    df: pd.DataFrame,
    marker_scale: float = 1.0,
) -> folium.FeatureGroup:
    """Build a replaceable GPU-backed point layer for Streamlit-Folium."""
    earthquake_layer = folium.FeatureGroup(name="Gempa", show=True)
    canvas_layer = MacroElement()
    canvas_layer._name = "EarthquakeCanvas"
    canvas_layer.records_json = json.dumps(
        _earthquake_canvas_records(df, marker_scale),
        ensure_ascii=True,
        separators=(",", ":"),
    )
    canvas_layer._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function () {
            const records = {{ this.records_json | safe }};
            const fillColors = ["#ed2939", "#f04b23", "#f5a623", "#087830", "#2534d2"];
            const borderColors = ["#991b1b", "#9a3412", "#92400e", "#064e3b", "#172554"];
            const depthLabels = ["<= 50 km", "> 50-100 km", "> 100-250 km", "> 250-600 km", "> 600 km"];
            const escapeHtml = function (value) {
                const element = document.createElement("div");
                element.textContent = String(value);
                return element.innerHTML;
            };
            const optionalLine = function (label, value, suffix) {
                if (value === null || value === undefined || value === "") return "";
                return "<br>" + label + escapeHtml(value) + (suffix || "");
            };
            const popupContent = function (record) {
                const controlId = "earthquake-visibility-" + record[16];
                let html = "<div style='display:flex;align-items:center;justify-content:space-between;" +
                    "gap:12px;margin-bottom:4px;padding-right:10px'>" +
                    "<b>Detail Gempa</b>" +
                    "<label style='display:flex;align-items:center;gap:4px;font-size:12px;" +
                    "font-weight:600;white-space:nowrap;cursor:pointer' title='Sembunyikan kejadian ini'>" +
                    "<input id='" + controlId + "' class='earthquake-visibility-toggle' " +
                    "type='checkbox' checked style='margin:0;accent-color:#ef4444'> Tampil" +
                    "</label></div>" +
                    "Tanggal: " + escapeHtml(record[4]) + "<br>" +
                    "Jam: " + escapeHtml(record[5]) + "<br>" +
                    "Magnitudo: " + Number(record[6]).toFixed(1) + "<br>" +
                    "Kedalaman: " + Number(record[7]).toFixed(1) + " km<br>" +
                    "Kelas: " + depthLabels[record[3]] + "<br>" +
                    "Koordinat: " + Number(record[0]).toFixed(4) + ", " + Number(record[1]).toFixed(4);
                if (record[8] !== null) {
                    html += optionalLine("Jejak sesar terdekat (geometris): ", record[8]);
                    html += optionalLine("Jarak horizontal episenter-jejak: ", Number(record[9]).toFixed(1), " km");
                }
                html += optionalLine("Provinsi: ", record[10]);
                html += optionalLine("Kabupaten/Kota: ", record[11]);
                html += optionalLine("Area episentrum: ", record[12]);
                if (record[13] !== null && Number(record[13]) > 0) {
                    html += optionalLine("Jarak ke daratan: ", Number(record[13]).toFixed(2), " km");
                }
                if (record[14] !== null && String(record[14]).trim()) {
                    html += "<br><b>Keterangan lokasi:</b> " + escapeHtml(record[14]);
                }
                html += optionalLine("Dirasakan: ", record[15]);
                return html;
            };
            const EarthquakePopup = L.Popup.extend({
                getEvents: function () {
                    const events = L.Popup.prototype.getEvents.call(this);
                    events.move = this._updatePosition;
                    events.resize = this._updatePosition;
                    return events;
                },
                _updatePosition: function () {
                    if (!this._map) return;
                    const position = this._map.latLngToContainerPoint(this._latlng);
                    let offset = L.point(this.options.offset);
                    const anchor = this._getAnchor();
                    if (this._zoomAnimated) {
                        L.DomUtil.setPosition(this._container, position.add(anchor));
                    } else {
                        offset = offset.add(position).add(anchor);
                    }
                    this._containerBottom = -offset.y;
                    this._containerLeft = -Math.round(this._containerWidth / 2) + offset.x;
                    this._container.style.bottom = this._containerBottom + "px";
                    this._container.style.left = this._containerLeft + "px";
                }
            });
            const EarthquakeWebGLLayer = L.Layer.extend({
                initialize: function (data) {
                    this._data = data;
                    this._gl = null;
                    this._fallback2d = false;
                    this._animationFrame = null;
                },
                onAdd: function (map) {
                    this._map = map;
                    this._canvas = L.DomUtil.create("canvas", "earthquake-webgl-layer");
                    this._canvas.style.pointerEvents = "none";
                    this._canvas.style.position = "absolute";
                    this._canvas.style.left = "0";
                    this._canvas.style.top = "0";
                    this._canvas.style.zIndex = "650";
                    map.getContainer().appendChild(this._canvas);
                    this._gl = this._canvas.getContext("webgl", {
                        alpha: true,
                        antialias: false,
                        premultipliedAlpha: true
                    });
                    if (this._gl) {
                        this._initializeWebGL();
                    } else {
                        this._fallback2d = true;
                    }
                    this._selectionMarker = L.circleMarker([0, 0], {
                        radius: 1,
                        opacity: 0,
                        fillOpacity: 0,
                        interactive: false,
                        bubblingMouseEvents: false,
                        pane: "earthquakePane"
                    }).addTo(map);
                    map.on("move zoom resize", this._scheduleReset, this);
                    map.on("click", this._onClick, this);
                    this._reset();
                },
                onRemove: function (map) {
                    map.off("move zoom resize", this._scheduleReset, this);
                    map.off("click", this._onClick, this);
                    if (this._animationFrame !== null) {
                        cancelAnimationFrame(this._animationFrame);
                        this._animationFrame = null;
                    }
                    map.closePopup();
                    if (this._selectionMarker) {
                        map.removeLayer(this._selectionMarker);
                        this._selectionMarker = null;
                    }
                    L.DomUtil.remove(this._canvas);
                },
                _scheduleReset: function () {
                    if (this._animationFrame !== null) return;
                    this._animationFrame = requestAnimationFrame(() => {
                        this._animationFrame = null;
                        this._reset();
                    });
                },
                _compileShader: function (type, source) {
                    const gl = this._gl;
                    const shader = gl.createShader(type);
                    gl.shaderSource(shader, source);
                    gl.compileShader(shader);
                    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
                        throw new Error("WebGL shader gempa gagal: " + gl.getShaderInfoLog(shader));
                    }
                    return shader;
                },
                _initializeWebGL: function () {
                    const gl = this._gl;
                    const vertexSource = [
                        "attribute vec2 a_world;",
                        "attribute float a_radius;",
                        "attribute float a_depth_class;",
                        "uniform float u_world_size;",
                        "uniform vec2 u_pixel_origin;",
                        "uniform vec2 u_view_size;",
                        "uniform float u_ratio;",
                        "varying float v_depth_class;",
                        "void main() {",
                        "  vec2 pixel = a_world * u_world_size - u_pixel_origin;",
                        "  vec2 clip = vec2((pixel.x / u_view_size.x) * 2.0 - 1.0,",
                        "                   1.0 - (pixel.y / u_view_size.y) * 2.0);",
                        "  gl_Position = vec4(clip, 0.0, 1.0);",
                        "  gl_PointSize = max(1.5, a_radius * 2.0 * u_ratio);",
                        "  v_depth_class = a_depth_class;",
                        "}"
                    ].join("\\n");
                    const fragmentSource = [
                        "precision mediump float;",
                        "varying float v_depth_class;",
                        "void main() {",
                        "  float distance_from_center = distance(gl_PointCoord, vec2(0.5));",
                        "  if (distance_from_center > 0.5) discard;",
                        "  vec3 fill; vec3 border;",
                        "  if (v_depth_class < 0.5) { fill=vec3(0.929,0.161,0.224); border=vec3(0.600,0.106,0.106); }",
                        "  else if (v_depth_class < 1.5) { fill=vec3(0.941,0.294,0.137); border=vec3(0.604,0.204,0.071); }",
                        "  else if (v_depth_class < 2.5) { fill=vec3(0.961,0.651,0.137); border=vec3(0.573,0.251,0.055); }",
                        "  else if (v_depth_class < 3.5) { fill=vec3(0.031,0.471,0.188); border=vec3(0.024,0.306,0.231); }",
                        "  else { fill=vec3(0.145,0.204,0.824); border=vec3(0.090,0.208,0.329); }",
                        "  bool edge = distance_from_center > 0.41;",
                        "  gl_FragColor = vec4(edge ? border : fill, edge ? 1.0 : 0.78);",
                        "}"
                    ].join("\\n");
                    const program = gl.createProgram();
                    gl.attachShader(program, this._compileShader(gl.VERTEX_SHADER, vertexSource));
                    gl.attachShader(program, this._compileShader(gl.FRAGMENT_SHADER, fragmentSource));
                    gl.linkProgram(program);
                    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
                        throw new Error("WebGL program gempa gagal: " + gl.getProgramInfoLog(program));
                    }
                    gl.useProgram(program);
                    this._program = program;
                    this._locations = {
                        worldSize: gl.getUniformLocation(program, "u_world_size"),
                        pixelOrigin: gl.getUniformLocation(program, "u_pixel_origin"),
                        viewSize: gl.getUniformLocation(program, "u_view_size"),
                        ratio: gl.getUniformLocation(program, "u_ratio")
                    };

                    const positions = new Float32Array(this._data.length * 2);
                    const radii = new Float32Array(this._data.length);
                    const depthClasses = new Float32Array(this._data.length);
                    for (let index = 0; index < this._data.length; index += 1) {
                        const record = this._data[index];
                        const latitude = Math.max(-85.05112878, Math.min(85.05112878, Number(record[0])));
                        const longitude = Number(record[1]);
                        const sinLatitude = Math.sin(latitude * Math.PI / 180);
                        positions[index * 2] = (longitude + 180) / 360;
                        positions[index * 2 + 1] = 0.5 - Math.log(
                            (1 + sinLatitude) / (1 - sinLatitude)
                        ) / (4 * Math.PI);
                        radii[index] = Number(record[2]);
                        depthClasses[index] = Number(record[3]);
                    }
                    const bindAttribute = function (name, values, size) {
                        const buffer = gl.createBuffer();
                        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
                        gl.bufferData(gl.ARRAY_BUFFER, values, gl.STATIC_DRAW);
                        const location = gl.getAttribLocation(program, name);
                        gl.enableVertexAttribArray(location);
                        gl.vertexAttribPointer(location, size, gl.FLOAT, false, 0, 0);
                        return buffer;
                    };
                    this._positionBuffer = bindAttribute("a_world", positions, 2);
                    this._radiusBuffer = bindAttribute("a_radius", radii, 1);
                    this._depthBuffer = bindAttribute("a_depth_class", depthClasses, 1);
                    gl.enable(gl.BLEND);
                    gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
                },
                _drawFallback: function (size, ratio) {
                    const context = this._canvas.getContext("2d");
                    if (!context) return;
                    context.setTransform(ratio, 0, 0, ratio, 0, 0);
                    context.clearRect(0, 0, size.x, size.y);
                    const bounds = this._map.getBounds().pad(0.08);
                    for (let index = 0; index < this._data.length; index += 1) {
                        const record = this._data[index];
                        if (!bounds.contains([record[0], record[1]])) continue;
                        const point = this._map.latLngToContainerPoint([record[0], record[1]]);
                        const radius = Math.max(0.75, record[2] - 0.5);
                        context.beginPath();
                        context.arc(point.x, point.y, radius, 0, Math.PI * 2);
                        context.fillStyle = fillColors[record[3]];
                        context.globalAlpha = 0.78;
                        context.fill();
                        context.globalAlpha = 1;
                        context.strokeStyle = borderColors[record[3]];
                        context.lineWidth = 1;
                        context.stroke();
                    }
                },
                _reset: function () {
                    const map = this._map;
                    const size = map.getSize();
                    const ratio = Math.max(1, window.devicePixelRatio || 1);
                    this._canvas.width = Math.round(size.x * ratio);
                    this._canvas.height = Math.round(size.y * ratio);
                    this._canvas.style.width = size.x + "px";
                    this._canvas.style.height = size.y + "px";
                    if (this._fallback2d || !this._gl) {
                        this._drawFallback(size, ratio);
                        return;
                    }
                    const gl = this._gl;
                    const origin = map.getPixelOrigin();
                    const panePosition = L.DomUtil.getPosition(map.getPane("mapPane"))
                        || L.point(0, 0);
                    gl.viewport(0, 0, this._canvas.width, this._canvas.height);
                    gl.clearColor(0, 0, 0, 0);
                    gl.clear(gl.COLOR_BUFFER_BIT);
                    gl.useProgram(this._program);
                    gl.uniform1f(this._locations.worldSize, 256 * Math.pow(2, map.getZoom()));
                    gl.uniform2f(
                        this._locations.pixelOrigin,
                        origin.x - panePosition.x,
                        origin.y - panePosition.y
                    );
                    gl.uniform2f(this._locations.viewSize, size.x, size.y);
                    gl.uniform1f(this._locations.ratio, ratio);
                    gl.drawArrays(gl.POINTS, 0, this._data.length);
                },
                _onClick: function (event) {
                    const point = this._map.latLngToContainerPoint(event.latlng);
                    let selected = null;
                    let bestDistance = Infinity;
                    const bounds = this._map.getBounds().pad(0.02);
                    for (let index = this._data.length - 1; index >= 0; index -= 1) {
                        const record = this._data[index];
                        if (!bounds.contains([record[0], record[1]])) continue;
                        const drawn = this._map.latLngToContainerPoint([record[0], record[1]]);
                        const deltaX = point.x - drawn.x;
                        const deltaY = point.y - drawn.y;
                        const distance = deltaX * deltaX + deltaY * deltaY;
                        const hitRadius = record[2] + 4;
                        if (distance <= hitRadius * hitRadius && distance < bestDistance) {
                            selected = index;
                            bestDistance = distance;
                        }
                    }
                    if (selected === null) return;
                    const record = this._data[selected];
                    const selectedLatLng = L.latLng(record[0], record[1]);
                    new EarthquakePopup({
                        maxWidth: 340,
                        pane: "earthquakePopupPane",
                        offset: L.point(0, Math.ceil(Number(record[2])) + 6)
                    })
                        .setLatLng(selectedLatLng)
                        .setContent(popupContent(record))
                        .openOn(this._map);
                    const visibilityToggle = document.getElementById(
                        "earthquake-visibility-" + record[16]
                    );
                    if (visibilityToggle) {
                        L.DomEvent.disableClickPropagation(visibilityToggle);
                        visibilityToggle.addEventListener("change", (toggleEvent) => {
                            toggleEvent.stopPropagation();
                            if (visibilityToggle.checked) return;
                            const actionToken = "hide:" + record[16] + ":" + Date.now();
                            this._selectionMarker
                                .setLatLng(selectedLatLng)
                                .unbindTooltip()
                                .bindTooltip(actionToken, {opacity: 0});
                            this._selectionMarker.fire("click", {latlng: selectedLatLng});
                        });
                    }
                }
            });
            const {{ this.get_name() }} = new EarthquakeWebGLLayer(records);
            {{ this.get_name() }}.addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
        """
    )
    canvas_layer.add_to(earthquake_layer)
    return earthquake_layer


def _add_earthquake_canvas_layer(
    earthquake_map: folium.Map,
    df: pd.DataFrame,
    marker_scale: float,
) -> None:
    build_earthquake_feature_group(df, marker_scale).add_to(earthquake_map)


SEISMIC_STATION_COLOR = "#f97316"
SEISMIC_STATION_BORDER = "#9a3412"
SEISMIC_STATION_LAYER_NAME = "Stasiun Seismik"


def _add_station_canvas_layer(
    earthquake_map: folium.Map,
    station_records: Collection[tuple[Any, ...]],
    marker_scale: float = 1.0,
    *,
    show: bool = True,
) -> None:
    """Register clickable station triangles as a Leaflet overlay.

    Each record is ``(lat, lon, code, network, site_name, elevation, start,
    end)``. Coordinates plot the triangle; the rest populates a popup shown when
    a station is clicked.
    """
    records = [
        [
            round(float(rec[0]), 6),
            round(float(rec[1]), 6),
            str(rec[2]) if len(rec) > 2 else "",
            str(rec[3]) if len(rec) > 3 else "",
            str(rec[4]) if len(rec) > 4 else "",
            str(rec[5]) if len(rec) > 5 else "",
            str(rec[6]) if len(rec) > 6 else "",
            str(rec[7]) if len(rec) > 7 else "",
        ]
        for rec in station_records
    ]
    if not records:
        return
    station_layer = folium.FeatureGroup(
        name=SEISMIC_STATION_LAYER_NAME,
        overlay=True,
        control=True,
        show=show,
    )
    station_layer.add_to(earthquake_map)
    canvas_layer = MacroElement()
    canvas_layer._name = "SeismicStationCanvas"
    canvas_layer.records_json = json.dumps(records, ensure_ascii=True, separators=(",", ":"))
    canvas_layer.fill_color = SEISMIC_STATION_COLOR
    canvas_layer.border_color = SEISMIC_STATION_BORDER
    canvas_layer.marker_scale = float(marker_scale)
    canvas_layer._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function () {
            const records = {{ this.records_json | safe }};
            const fillColor = "{{ this.fill_color }}";
            const borderColor = "{{ this.border_color }}";
            const radius = 6 * {{ this.marker_scale }};
            const escapeHtml = function (value) {
                const element = document.createElement("div");
                element.textContent = String(value === null || value === undefined ? "" : value);
                return element.innerHTML;
            };
            const optionalLine = function (label, value) {
                if (value === null || value === undefined || String(value).trim() === "") return "";
                return "<br>" + label + escapeHtml(value);
            };
            const popupContent = function (record) {
                let html = "<b>Stasiun Seismik</b><br>" +
                    "Kode: " + escapeHtml(record[2]);
                if (record[3]) html += " (" + escapeHtml(record[3]) + ")";
                html += optionalLine("Nama: ", record[4]);
                html += "<br>Koordinat: " + Number(record[0]).toFixed(4) + ", " + Number(record[1]).toFixed(4);
                html += optionalLine("Elevasi: ", record[5] ? record[5] + " m" : "");
                html += optionalLine("Mulai: ", record[6]);
                html += optionalLine("Selesai: ", record[7]);
                return html;
            };
            const StationCanvasLayer = L.Layer.extend({
                initialize: function (data) {
                    this._data = data;
                    this._drawn = [];
                },
                onAdd: function (map) {
                    this._map = map;
                    this._canvas = L.DomUtil.create("canvas", "station-canvas-layer");
                    this._canvas.style.pointerEvents = "none";
                    map.getPane("overlayPane").appendChild(this._canvas);
                    map.on("moveend zoomend resize", this._reset, this);
                    map.on("click", this._onClick, this);
                    this._reset();
                },
                onRemove: function (map) {
                    map.off("moveend zoomend resize", this._reset, this);
                    map.off("click", this._onClick, this);
                    L.DomUtil.remove(this._canvas);
                    this._drawn = [];
                },
                _reset: function () {
                    const map = this._map;
                    const size = map.getSize();
                    const ratio = Math.max(1, window.devicePixelRatio || 1);
                    const topLeft = map.containerPointToLayerPoint([0, 0]);
                    L.DomUtil.setPosition(this._canvas, topLeft);
                    this._canvas.width = Math.round(size.x * ratio);
                    this._canvas.height = Math.round(size.y * ratio);
                    this._canvas.style.width = size.x + "px";
                    this._canvas.style.height = size.y + "px";
                    const context = this._canvas.getContext("2d");
                    context.setTransform(ratio, 0, 0, ratio, 0, 0);
                    context.clearRect(0, 0, size.x, size.y);
                    const bounds = map.getBounds().pad(0.1);
                    const south = bounds.getSouth();
                    const north = bounds.getNorth();
                    const west = bounds.getWest();
                    const east = bounds.getEast();
                    this._drawn = [];
                    for (let index = 0; index < this._data.length; index += 1) {
                        const record = this._data[index];
                        if (record[0] < south || record[0] > north || record[1] < west || record[1] > east) continue;
                        const point = map.latLngToContainerPoint([record[0], record[1]]);
                        context.beginPath();
                        context.moveTo(point.x, point.y - radius);
                        context.lineTo(point.x - radius * 0.9, point.y + radius * 0.72);
                        context.lineTo(point.x + radius * 0.9, point.y + radius * 0.72);
                        context.closePath();
                        context.fillStyle = fillColor;
                        context.globalAlpha = 0.92;
                        context.fill();
                        context.globalAlpha = 1;
                        context.strokeStyle = borderColor;
                        context.lineWidth = 1;
                        context.stroke();
                        this._drawn.push([point.x, point.y, index]);
                    }
                },
                _onClick: function (event) {
                    const point = this._map.latLngToContainerPoint(event.latlng);
                    let selected = null;
                    let bestDistance = Infinity;
                    const hitRadius = radius + 5;
                    for (let i = this._drawn.length - 1; i >= 0; i -= 1) {
                        const drawn = this._drawn[i];
                        const dx = point.x - drawn[0];
                        const dy = point.y - drawn[1];
                        const distance = dx * dx + dy * dy;
                        if (distance <= hitRadius * hitRadius && distance < bestDistance) {
                            selected = drawn[2];
                            bestDistance = distance;
                        }
                    }
                    if (selected === null) return;
                    const record = this._data[selected];
                    L.popup({maxWidth: 300})
                        .setLatLng([record[0], record[1]])
                        .setContent(popupContent(record))
                        .openOn(this._map);
                }
            });
            new StationCanvasLayer(records).addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
        """
    )
    canvas_layer.add_to(station_layer)


def classify_fault_type(type_code: str | None) -> str:
    """Map PUSGEN fault codes to the cartographic fault classes."""
    normalized = str(type_code or "").strip().upper()
    if normalized.startswith("SS"):
        return "Sesar Geser"
    if normalized.startswith("R"):
        return "Sesar Naik"
    if normalized.startswith("N"):
        return "Sesar Normal"
    return "Sesar"


FAULT_DIRECTION_VECTORS = {
    "N": (0.0, -1.0),
    "NE": (math.sqrt(0.5), -math.sqrt(0.5)),
    "E": (1.0, 0.0),
    "SE": (math.sqrt(0.5), math.sqrt(0.5)),
    "S": (0.0, 1.0),
    "SW": (-math.sqrt(0.5), math.sqrt(0.5)),
    "W": (-1.0, 0.0),
    "NW": (-math.sqrt(0.5), -math.sqrt(0.5)),
}


def get_fault_dip_direction(type_code: str | None) -> str | None:
    """Read the PUSGEN dip quadrant embedded at the end of R/N type codes."""
    normalized = str(type_code or "").strip().upper().replace(" ", "")
    if classify_fault_type(normalized) not in {"Sesar Naik", "Sesar Normal"}:
        return None
    match = re.search(r"(NE|NW|SE|SW|N|E|S|W)$", normalized)
    return match.group(1) if match else None


def get_fault_symbol_side(
    type_code: str | None,
    unit_x: float,
    unit_y: float,
) -> int:
    """Choose the local normal that points toward the PUSGEN dip direction."""
    direction = get_fault_dip_direction(type_code)
    if direction is None:
        # Previous rendering always used +normal. Reverse ambiguous records as
        # requested, while directional records are anchored to PUSGEN metadata.
        return -1
    target_x, target_y = FAULT_DIRECTION_VECTORS[direction]
    normal_x, normal_y = -unit_y, unit_x
    return 1 if normal_x * target_x + normal_y * target_y >= 0 else -1


def get_strike_slip_sense(type_code: str | None) -> int:
    """Return +1 for right-lateral and -1 for left/unspecified slip arrows."""
    normalized = str(type_code or "").strip().upper().replace(" ", "")
    if "RL" in normalized:
        return 1
    # The source table explicitly marks LL where known. Unqualified SS records
    # use the reversed convention requested by the user instead of the old one.
    return -1


def _fault_css_class(type_code: str | None) -> str:
    fault_class = classify_fault_type(type_code)
    return {
        "Sesar Geser": "strike-slip",
        "Sesar Naik": "reverse",
        "Sesar Normal": "normal",
    }.get(fault_class, "generic")


def _fault_style(feature: dict[str, Any]) -> dict[str, Any]:
    fault_code = feature.get("properties", {}).get("Type")
    return {
        "color": "#c2185b",
        "weight": 2.8,
        "opacity": 0.95,
        "className": f"fault-trace fault-trace-{_fault_css_class(fault_code)}",
    }


def _point_along_line(
    coordinates: list[list[float]],
    target_distance: float,
) -> list[float]:
    remaining = max(0.0, target_distance)
    for start, end in zip(coordinates, coordinates[1:]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length <= 0:
            continue
        if remaining <= length:
            fraction = remaining / length
            return [
                start[0] + (end[0] - start[0]) * fraction,
                start[1] + (end[1] - start[1]) * fraction,
            ]
        remaining -= length
    return list(coordinates[-1][:2])


def _fault_symbol_segments(
    coordinates: list[list[float]],
    *,
    repeat: bool,
) -> list[list[list[float]]]:
    valid_coordinates = [point for point in coordinates if len(point) >= 2]
    if len(valid_coordinates) < 2:
        return []
    total_length = sum(
        math.hypot(end[0] - start[0], end[1] - start[1])
        for start, end in zip(valid_coordinates, valid_coordinates[1:])
    )
    if total_length <= 0:
        return []
    symbol_count = (
        min(7, max(3, math.ceil(total_length / 0.35)))
        if repeat
        else 1
    )
    interval = total_length / (symbol_count + 1)
    offset = min(interval * 0.18, 0.04)
    return [
        [
            _point_along_line(valid_coordinates, target_distance - offset),
            _point_along_line(valid_coordinates, target_distance),
            _point_along_line(valid_coordinates, target_distance + offset),
        ]
        for target_distance in (
            interval * (index + 1) for index in range(symbol_count)
        )
    ]


def _fault_symbol_canvas_records(
    fault_geojson: dict[str, Any],
) -> list[list[float | int]]:
    type_indexes = {"strike-slip": 0, "reverse": 1, "normal": 2}
    records: list[list[float | int]] = []
    for feature in fault_geojson.get("features", []):
        type_code = feature.get("properties", {}).get("Type")
        fault_type = _fault_css_class(type_code)
        if fault_type not in type_indexes:
            continue
        for coordinates in _iter_geojson_lines(feature.get("geometry") or {}):
            segments = _fault_symbol_segments(
                coordinates,
                repeat=fault_type in {"reverse", "normal"},
            )
            for before, midpoint, after in segments:
                angle = math.degrees(
                    math.atan2(-(after[1] - before[1]), after[0] - before[0])
                )
                angle_radians = math.radians(angle)
                records.append(
                    [
                        round(midpoint[1], 6),
                        round(midpoint[0], 6),
                        round(angle, 2),
                        type_indexes[fault_type],
                        get_fault_symbol_side(
                            type_code,
                            math.cos(angle_radians),
                            math.sin(angle_radians),
                        ),
                        get_strike_slip_sense(type_code),
                    ]
                )
    return records


def get_fault_label_records(
    fault_geojson: dict[str, Any],
) -> list[tuple[str, float, float]]:
    """Return one stable label position per named fault, never per segment."""
    longest_by_name: dict[str, tuple[float, list[list[float]]]] = {}
    for feature in fault_geojson.get("features", []):
        name = str((feature.get("properties") or {}).get("Name") or "").strip()
        if not name:
            continue
        for coordinates in _iter_geojson_lines(feature.get("geometry") or {}):
            length = sum(
                math.hypot(end[0] - start[0], end[1] - start[1])
                for start, end in zip(coordinates, coordinates[1:])
                if len(start) >= 2 and len(end) >= 2
            )
            if length > longest_by_name.get(name, (-1.0, []))[0]:
                longest_by_name[name] = (length, coordinates)
    labels: list[tuple[str, float, float]] = []
    for name, (length, coordinates) in sorted(longest_by_name.items()):
        if length <= 0 or len(coordinates) < 2:
            continue
        longitude, latitude = _point_along_line(coordinates, length / 2)
        labels.append((name, float(latitude), float(longitude)))
    return labels


def _add_fault_labels(
    fault_layer: folium.FeatureGroup,
    fault_geojson: dict[str, Any],
    earthquake_map: folium.Map,
) -> None:
    for name, latitude, longitude in get_fault_label_records(fault_geojson):
        folium.Marker(
            [latitude, longitude],
            icon=folium.DivIcon(
                class_name="fault-name-label",
                html=(
                    '<div style="white-space:nowrap;transform:translate(-50%,-50%);'
                    'font:700 10px Arial,sans-serif;color:#000;letter-spacing:.1px;'
                    'text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,'
                    '1px 1px 0 #fff,0 0 3px #fff;">'
                    f"{escape(name)}</div>"
                ),
            ),
            interactive=False,
        ).add_to(fault_layer)
    visibility = MacroElement()
    visibility._name = "FaultLabelZoomVisibility"
    visibility._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function () {
            const map = {{ this._parent.get_name() }};
            const updateFaultLabels = function () {
                const visible = map.getZoom() >= 9;
                map.getContainer().querySelectorAll('.fault-name-label').forEach(function (label) {
                    label.style.display = visible ? '' : 'none';
                });
            };
            map.on('zoomend layeradd', updateFaultLabels);
            window.setTimeout(updateFaultLabels, 0);
        })();
        {% endmacro %}
        """
    )
    visibility.add_to(earthquake_map)


def _add_fault_symbol_canvas_layer(
    fault_layer: folium.FeatureGroup,
    fault_geojson: dict[str, Any],
) -> None:
    canvas_layer = MacroElement()
    canvas_layer._name = "FaultSymbolCanvas"
    canvas_layer.records_json = json.dumps(
        _fault_symbol_canvas_records(fault_geojson),
        separators=(",", ":"),
    )
    canvas_layer._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function () {
            const records = {{ this.records_json | safe }};
            const FaultSymbolCanvasLayer = L.Layer.extend({
                initialize: function (data) {
                    this._data = data;
                },
                onAdd: function (map) {
                    this._map = map;
                    this._canvas = L.DomUtil.create("canvas", "fault-symbol-canvas-layer");
                    this._canvas.style.pointerEvents = "none";
                    map.getPanes().overlayPane.appendChild(this._canvas);
                    map.on("moveend zoomend resize", this._reset, this);
                    this._reset();
                },
                onRemove: function (map) {
                    map.off("moveend zoomend resize", this._reset, this);
                    L.DomUtil.remove(this._canvas);
                },
                _reset: function () {
                    const map = this._map;
                    const size = map.getSize();
                    const ratio = Math.max(1, window.devicePixelRatio || 1);
                    const topLeft = map.containerPointToLayerPoint([0, 0]);
                    L.DomUtil.setPosition(this._canvas, topLeft);
                    this._canvas.width = Math.round(size.x * ratio);
                    this._canvas.height = Math.round(size.y * ratio);
                    this._canvas.style.width = size.x + "px";
                    this._canvas.style.height = size.y + "px";
                    const context = this._canvas.getContext("2d");
                    context.setTransform(ratio, 0, 0, ratio, 0, 0);
                    context.clearRect(0, 0, size.x, size.y);
                    context.strokeStyle = "#c2185b";
                    context.fillStyle = "#c2185b";
                    context.lineWidth = 1.7;
                    context.lineCap = "round";
                    context.lineJoin = "round";
                    const bounds = map.getBounds().pad(0.08);
                    const south = bounds.getSouth();
                    const north = bounds.getNorth();
                    const west = bounds.getWest();
                    const east = bounds.getEast();
                    for (let index = 0; index < this._data.length; index += 1) {
                        const record = this._data[index];
                        if (record[0] < south || record[0] > north || record[1] < west || record[1] > east) continue;
                        const point = map.latLngToContainerPoint([record[0], record[1]]);
                        context.save();
                        context.translate(point.x, point.y);
                        context.rotate(record[2] * Math.PI / 180);
                        context.beginPath();
                        if (record[3] === 0) {
                            const sense = record[5] || -1;
                            const upperTip = 9 * sense;
                            const lowerTip = -9 * sense;
                            context.moveTo(-11 * sense, -4); context.lineTo(upperTip, -4);
                            context.moveTo(upperTip, -4); context.lineTo(upperTip - 4 * sense, -7);
                            context.moveTo(upperTip, -4); context.lineTo(upperTip - 4 * sense, -1);
                            context.moveTo(11 * sense, 4); context.lineTo(lowerTip, 4);
                            context.moveTo(lowerTip, 4); context.lineTo(lowerTip + 4 * sense, 1);
                            context.moveTo(lowerTip, 4); context.lineTo(lowerTip + 4 * sense, 7);
                            context.stroke();
                        } else if (record[3] === 1) {
                            const side = record[4] || -1;
                            context.moveTo(-5, 2 * side);
                            context.lineTo(5, 2 * side);
                            context.lineTo(0, 9 * side);
                            context.closePath();
                            context.fill();
                        } else {
                            const side = record[4] || -1;
                            context.fillRect(-3.5, side > 0 ? 3 : -10, 7, 7);
                        }
                        context.restore();
                    }
                }
            });
            const {{ this.get_name() }} = new FaultSymbolCanvasLayer(records);
            {{ this.get_name() }}.addTo({{ this._parent.get_name() }});
        })();
        {% endmacro %}
        """
    )
    canvas_layer.add_to(fault_layer)


def _iter_geojson_lines(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "LineString":
        return [coordinates]
    if geometry_type == "MultiLineString":
        return coordinates
    return []


def _add_volcano_layer(
    earthquake_map: folium.Map,
    volcano_geojson: dict[str, Any],
    volcano_names: Collection[str] | None,
) -> bool:
    """Add selectable official BIG KRB polygons, with point fallback."""
    visible_volcanoes = filter_volcano_features(volcano_geojson, volcano_names)
    features = visible_volcanoes.get("features") or []
    if not features:
        return False

    volcano_layer = folium.FeatureGroup(
        name="Kawasan Rawan Bencana Gunung Api (BIG)",
        show=True,
    )
    polygon_features = [
        feature
        for feature in features
        if (feature.get("geometry") or {}).get("type")
        in {"Polygon", "MultiPolygon"}
    ]
    if polygon_features:
        def _krb_style(feature: dict[str, Any]) -> dict[str, Any]:
            hazard_class = int((feature.get("properties") or {}).get("indga") or 0)
            color = {1: "#ca8a04", 2: "#be185d", 3: "#991b1b"}.get(
                hazard_class,
                "#475569",
            )
            fill_color = {1: "#facc15", 2: "#f472b6", 3: "#ef4444"}.get(
                hazard_class,
                "#94a3b8",
            )
            return {
                "color": color,
                "fillColor": fill_color,
                "fillOpacity": 0.2,
                "weight": 2.2,
                "opacity": 0.95,
            }

        folium.GeoJson(
            {"type": "FeatureCollection", "features": polygon_features},
            style_function=_krb_style,
            highlight_function=lambda _feature: {
                "color": "#450a0a",
                "fillOpacity": 0.3,
                "weight": 3.2,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["display_name"],
                aliases=["Gunung api:"],
                labels=True,
                sticky=False,
            ),
            popup=folium.GeoJsonPopup(
                fields=["display_name", "krb_label", "thterbit", "remark"],
                aliases=["Gunung api", "Kawasan", "Tahun peta", "Keterangan"],
                localize=True,
                max_width=360,
            ),
        ).add_to(volcano_layer)
        volcano_layer.add_to(earthquake_map)
        return True

    points = list(iter_volcano_points(visible_volcanoes))
    for feature, latitude, longitude in points:
        properties = feature.get("properties") or {}
        name = get_volcano_feature_name(feature) or "Gunung api tanpa nama"
        volcano_type = str(properties.get("TIPE") or "-")
        elevation = properties.get("ELEVASI")
        if elevation in (None, ""):
            elevation = properties.get("KETINGGIAN")
        elevation_label = str(elevation) if elevation not in (None, "") else "-"
        popup_html = (
            f"<strong>{escape(name)}</strong><br>"
            f"Tipe: {escape(volcano_type)}<br>"
            f"Elevasi: {escape(elevation_label)}"
        )
        folium.RegularPolygonMarker(
            location=[latitude, longitude],
            number_of_sides=3,
            radius=8,
            rotation=0,
            color="#7f1d1d",
            weight=1.5,
            opacity=1.0,
            fill=True,
            fill_color="#ef4444",
            fill_opacity=0.9,
            tooltip=folium.Tooltip(f"Gunung api: {escape(name)}", sticky=False),
            popup=folium.Popup(popup_html, max_width=280),
        ).add_to(volcano_layer)
    volcano_layer.add_to(earthquake_map)
    return True


def _volcano_area_bounds(
    volcano_geojson: dict[str, Any],
    volcano_names: Collection[str] | None,
) -> list[list[float]] | None:
    """Return map bounds covering selected official KRB polygons."""
    if not volcano_names:
        return None
    visible = filter_volcano_features(volcano_geojson, volcano_names)
    bounds = [
        feature_bounds
        for feature in visible.get("features", [])
        if (
            feature_bounds := _geometry_bounds(feature.get("geometry") or {})
        )
        is not None
    ]
    if not bounds:
        return None
    return [
        [min(bound[1] for bound in bounds), min(bound[0] for bound in bounds)],
        [max(bound[3] for bound in bounds), max(bound[2] for bound in bounds)],
    ]


def _add_tectonic_legend(
    earthquake_map: folium.Map,
    *,
    show_faults: bool,
    show_trenches: bool,
    show_slabs: bool = False,
) -> None:
    rows: list[str] = []
    if show_trenches:
        rows.append(
            """<div class="tectonic-row"><svg viewBox="0 0 72 20" aria-hidden="true">
            <line x1="4" y1="15" x2="68" y2="15" stroke="#1f2937" stroke-width="2"/>
            <path d="M14 11L21 1L28 11M34 11L41 1L48 11M54 11L61 1L68 11" fill="#1f2937"/>
            </svg><span>Tunjaman</span></div>"""
        )
    if show_slabs:
        rows.append(
            f"""<div class="tectonic-row"><svg viewBox="0 0 72 18" aria-hidden="true">
            <line x1="4" y1="9" x2="68" y2="9" stroke="{SLAB_CONTOUR_COLOR}"
            stroke-width="2" stroke-dasharray="6 5"/>
            </svg><span>Kontur slab (km)</span></div>"""
        )
    if show_faults:
        rows.extend(
            [
                """<div class="tectonic-row"><svg viewBox="0 0 72 18" aria-hidden="true">
                <line x1="4" y1="9" x2="68" y2="9" stroke="#c2185b" stroke-width="2.4"/>
                <path d="M12 5h18l-5-4M30 13H12l5 4M60 5H42l5-4M42 13h18l-5 4" fill="none" stroke="#c2185b" stroke-width="1.6"/>
                </svg><span>Sesar Geser</span></div>""",
                """<div class="tectonic-row"><svg viewBox="0 0 72 18" aria-hidden="true">
                <line x1="4" y1="7" x2="68" y2="7" stroke="#c2185b" stroke-width="2.2"/>
                <path d="M17 8L26 8L21.5 15ZM41 8L50 8L45.5 15Z" fill="#c2185b"/>
                </svg><span>Sesar Naik</span></div>""",
                """<div class="tectonic-row"><svg viewBox="0 0 72 18" aria-hidden="true">
                <line x1="4" y1="6" x2="68" y2="6" stroke="#c2185b" stroke-width="2.2"/>
                <rect x="18" y="8" width="8" height="7" fill="#c2185b"/>
                <rect x="42" y="8" width="8" height="7" fill="#c2185b"/>
                </svg><span>Sesar Turun / Normal</span></div>""",
            ]
        )
    if not rows:
        return
    legend_html = f"""
    <details class="map-legend map-legend-tectonic">
      <summary>Legenda Tektonik</summary>
      <div class="map-legend-body">
        <style>.tectonic-row{{display:grid;grid-template-columns:72px auto;align-items:center;gap:7px;min-height:22px;}}
        .tectonic-row svg{{width:72px;height:20px;overflow:visible;}}</style>
        {''.join(rows)}
      </div>
    </details>
    """
    earthquake_map.get_root().html.add_child(folium.Element(legend_html))


SLAB_CONTOUR_COLOR = "#5b21b6"


def _slab_midpoint(coordinates: list[list[float]]) -> list[float]:
    point = coordinates[len(coordinates) // 2]
    return [float(point[1]), float(point[0])]


def _add_slab_layer(
    earthquake_map: folium.Map,
    slab_geojson: dict[str, Any],
) -> None:
    """Draw Slab2 depth contours as labelled dashed lines."""
    slab_layer = folium.FeatureGroup(name="Kontur Slab (Slab2)", show=True)
    for feature in slab_geojson.get("features", []):
        depth = feature.get("properties", {}).get("depth")
        if depth is None:
            continue
        depth_label = f"{float(depth):.0f} km"
        for coordinates in _iter_geojson_lines(feature.get("geometry") or {}):
            locations = [
                [float(point[1]), float(point[0])]
                for point in coordinates
                if len(point) >= 2
            ]
            if len(locations) < 2:
                continue
            folium.PolyLine(
                locations,
                color=SLAB_CONTOUR_COLOR,
                weight=1.6,
                opacity=0.85,
                dash_array="6,5",
                tooltip=folium.Tooltip(f"Slab {depth_label}", sticky=False),
            ).add_to(slab_layer)
            folium.Marker(
                location=_slab_midpoint(coordinates),
                icon=folium.DivIcon(
                    class_name="slab-depth-label",
                    html=(
                        '<div style="white-space:nowrap;font:700 10px Arial,sans-serif;'
                        "color:#000;text-shadow:-1px -1px 0 #fff,"
                        "1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff;"
                        'transform:translate(-50%,-50%);">'
                        f"{escape(depth_label)}</div>"
                    ),
                ),
                interactive=False,
            ).add_to(slab_layer)
    slab_layer.add_to(earthquake_map)


def _add_trench_layer(
    earthquake_map: folium.Map,
    trench_geojson: dict[str, Any],
) -> None:
    trench_layer = folium.FeatureGroup(name="Trench / Tunjaman", show=True)
    for feature in trench_geojson.get("features", []):
        name = str(feature.get("properties", {}).get("Name") or "Trench")
        for coordinates in _iter_geojson_lines(feature.get("geometry") or {}):
            locations = [
                [float(point[1]), float(point[0])]
                for point in coordinates
                if len(point) >= 2
            ]
            if len(locations) < 2:
                continue
            trench_line = folium.PolyLine(
                locations,
                color="#1f2937",
                weight=2.2,
                opacity=0.95,
                tooltip=folium.Tooltip(name, sticky=False),
            ).add_to(trench_layer)
            PolyLineTextPath(
                trench_line,
                "  \u25b2  ",
                repeat=True,
                center=False,
                offset=-3,
                attributes={
                    "fill": "#1f2937",
                    "font-size": "15",
                    "font-weight": "700",
                },
            ).add_to(trench_layer)
    trench_layer.add_to(earthquake_map)


def get_basemap_attribution(
    basemap_name: str,
    *,
    show_shortbread_topography: bool = False,
    html: bool = True,
) -> str:
    """Return the attribution text for a configured basemap."""
    if basemap_name == BASEMAP_RELIEF:
        attribution = RELIEF_ATTRIBUTION
    elif basemap_name == BASEMAP_CYCLOSM:
        attribution = CYCLOSM_ATTRIBUTION
    elif basemap_name == BASEMAP_HUMANITARIAN:
        attribution = HUMANITARIAN_ATTRIBUTION
    elif basemap_name == BASEMAP_SHORTBREAD:
        attribution = SHORTBREAD_ATTRIBUTION
        if show_shortbread_topography:
            attribution = f"{attribution} | {HILLSHADE_ATTRIBUTION}"
    else:
        attribution = OPENTOPO_ATTRIBUTION
    return attribution if html else attribution.replace("&copy;", "(c)")


def get_depth_style(depth_km: float) -> tuple[str, str, str]:
    """Return fill color, border color, and class label for a depth value."""
    if depth_km <= 50:
        return "#ed2939", "#991b1b", "<= 50 km"
    if depth_km <= 100:
        return "#f04b23", "#9a3412", "> 50-100 km"
    if depth_km <= 250:
        return "#f5a623", "#92400e", "> 100-250 km"
    if depth_km <= 600:
        return "#087830", "#064e3b", "> 250-600 km"
    return "#2534d2", "#172554", "> 600 km"


def get_magnitude_marker(magnitude: float) -> tuple[float, str]:
    """Return a discrete radius and legend class for magnitude 1 through 8+."""
    if magnitude >= 8:
        return 15.0, "8+"
    magnitude_class = max(1, int(math.floor(magnitude)))
    radii = {1: 3.5, 2: 4.5, 3: 5.8, 4: 7.2, 5: 8.8, 6: 10.5, 7: 12.5}
    return radii[magnitude_class], str(magnitude_class)


def get_scaled_magnitude_radius(magnitude: float, marker_scale: float = 1.0) -> float:
    """Return the plotted marker radius after applying the display scale."""
    base_radius, _ = get_magnitude_marker(magnitude)
    return round(base_radius * marker_scale, 2)


def _add_earthquake_legend(
    earthquake_map: folium.Map,
    marker_scale: float = 1.0,
) -> None:
    magnitude_items: list[str] = []
    maximum_diameter = 0.0
    for magnitude in range(1, 9):
        radius = get_scaled_magnitude_radius(float(magnitude), marker_scale)
        _, label = get_magnitude_marker(float(magnitude))
        diameter = radius * 2
        maximum_diameter = max(maximum_diameter, diameter)
        magnitude_items.append(
            '<div style="text-align:center;">'
            f'<span data-radius-px="{radius:g}" style="display:block;'
            f'width:{diameter:g}px;height:{diameter:g}px;box-sizing:border-box;'
            'border-radius:50%;background:#e5e7eb;border:1px solid #374151;'
            f'margin:auto;"></span>{label}</div>'
        )

    magnitude_legend = "".join(magnitude_items)
    magnitude_row_height = math.ceil(maximum_diameter) + 18
    legend_html = f"""
    <details class="map-legend map-legend-earthquake">
      <summary>Legenda Gempa</summary>
      <div class="map-legend-body">
        <div style="font-weight:600;margin-bottom:3px;">Magnitudo</div>
        <div style="display:flex;align-items:flex-end;gap:7px;height:{magnitude_row_height}px;margin-bottom:7px;">
            {magnitude_legend}
        </div>
        <div style="font-weight:600;margin-bottom:3px;">Kedalaman (km)</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px 10px;">
            <div><span style="display:inline-block;width:11px;height:11px;background:#ed2939;border:1px solid #991b1b;margin-right:5px;"></span>&le; 50</div>
            <div><span style="display:inline-block;width:11px;height:11px;background:#f04b23;border:1px solid #9a3412;margin-right:5px;"></span>&gt; 50-100</div>
            <div><span style="display:inline-block;width:11px;height:11px;background:#f5a623;border:1px solid #92400e;margin-right:5px;"></span>&gt; 100-250</div>
            <div><span style="display:inline-block;width:11px;height:11px;background:#087830;border:1px solid #064e3b;margin-right:5px;"></span>&gt; 250-600</div>
            <div><span style="display:inline-block;width:11px;height:11px;background:#2534d2;border:1px solid #172554;margin-right:5px;"></span>&gt; 600</div>
        </div>
      </div>
    </details>
    """
    earthquake_map.get_root().html.add_child(folium.Element(legend_html))


def _filter_fault_features(
    fault_geojson: dict[str, Any],
    fault_names: Collection[str] | None,
    fault_feature_indexes: Collection[int] | None = None,
) -> dict[str, Any]:
    if fault_names is None and fault_feature_indexes is None:
        return fault_geojson

    selected_names = None if fault_names is None else set(fault_names)
    selected_indexes = (
        None if fault_feature_indexes is None else set(fault_feature_indexes)
    )
    return {
        "type": "FeatureCollection",
        "features": [
            feature
            for feature_index, feature in enumerate(fault_geojson.get("features", []))
            if selected_indexes is None or feature_index in selected_indexes
            if selected_names is None
            or feature.get("properties", {}).get("Name") in selected_names
        ],
    }


def _filter_province_features(
    province_geojson: dict[str, Any],
    province_names: Collection[str] | None,
) -> dict[str, Any]:
    if not province_names:
        return province_geojson

    selected_names = set(province_names)
    return {
        "type": "FeatureCollection",
        "features": [
            feature
            for feature in province_geojson.get("features", [])
            if feature.get("properties", {}).get("PROVINSI") in selected_names
        ],
    }


def _fit_selected_provinces(
    earthquake_map: folium.Map,
    province_geojson: dict[str, Any],
    province_names: Collection[str],
) -> None:
    visible_provinces = _filter_province_features(province_geojson, province_names)
    positions: list[tuple[float, float]] = []

    def collect(value: Any) -> None:
        if not isinstance(value, list):
            return
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            positions.append((float(value[0]), float(value[1])))
            return
        for child in value:
            collect(child)

    for feature in visible_provinces.get("features", []):
        collect((feature.get("geometry") or {}).get("coordinates", []))
    if positions:
        fit_map_bounds(
            earthquake_map,
            [
                [min(latitude for _, latitude in positions), min(longitude for longitude, _ in positions)],
                [max(latitude for _, latitude in positions), max(longitude for longitude, _ in positions)],
            ],
            padding=(24, 24),
        )


def _ring_centroid(ring: list[list[float]]) -> tuple[float, float]:
    area_twice = 0.0
    longitude_sum = 0.0
    latitude_sum = 0.0
    for start, end in zip(ring, ring[1:]):
        cross = float(start[0]) * float(end[1]) - float(end[0]) * float(start[1])
        area_twice += cross
        longitude_sum += (float(start[0]) + float(end[0])) * cross
        latitude_sum += (float(start[1]) + float(end[1])) * cross

    if abs(area_twice) < 1e-12:
        longitude = sum(float(point[0]) for point in ring) / len(ring)
        latitude = sum(float(point[1]) for point in ring) / len(ring)
        return latitude, longitude

    longitude = longitude_sum / (3.0 * area_twice)
    latitude = latitude_sum / (3.0 * area_twice)
    return latitude, longitude


def _largest_outer_ring(geometry: dict[str, Any]) -> list[list[float]] | None:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    if geometry_type == "Polygon" and coordinates:
        return coordinates[0]
    if geometry_type == "MultiPolygon" and coordinates:
        return max(
            (polygon[0] for polygon in coordinates if polygon),
            key=lambda ring: abs(
                sum(
                    float(start[0]) * float(end[1])
                    - float(end[0]) * float(start[1])
                    for start, end in zip(ring, ring[1:])
                )
            ),
            default=None,
        )
    return None


def _add_province_labels(
    earthquake_map: folium.Map,
    province_geojson: dict[str, Any],
    province_names: Collection[str] | None,
    font_px: int = 11,
) -> None:
    labels = folium.FeatureGroup(name="Nama Provinsi", show=True)
    visible_provinces = _filter_province_features(province_geojson, province_names)
    halo = (
        "text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,1px 1px 0 #fff,"
        "0 0 3px #fff;"
    )
    for feature in visible_provinces.get("features", []):
        name = feature.get("properties", {}).get("PROVINSI")
        ring = _largest_outer_ring(feature.get("geometry") or {})
        if not name or not ring:
            continue
        latitude, longitude = _ring_centroid(ring)
        folium.Marker(
            location=[latitude, longitude],
            icon=folium.DivIcon(
                class_name="province-label",
                html=(
                    f'<div style="white-space:nowrap;font:700 {int(font_px)}px '
                    f'Arial,sans-serif;letter-spacing:0.3px;color:#000;{halo}'
                    'transform:translate(-50%,-50%);">'
                    f"{escape(str(name))}</div>"
                ),
            ),
            interactive=False,
        ).add_to(labels)
    labels.add_to(earthquake_map)


def _filter_district_features(
    district_geojson: dict[str, Any],
    province_names: Collection[str] | None = None,
) -> dict[str, Any]:
    selected_provinces = set(province_names or [])
    if not selected_provinces:
        return district_geojson
    return {
        "type": "FeatureCollection",
        "features": [
            feature
            for feature in district_geojson.get("features", [])
            if str((feature.get("properties") or {}).get("WADMPR") or "")
            in selected_provinces
        ],
    }


def _fit_selected_districts(
    earthquake_map: folium.Map,
    district_geojson: dict[str, Any],
    district_names: Collection[str],
) -> None:
    selected = set(district_names)
    bounds = [
        geometry_bounds
        for feature in district_geojson.get("features", [])
        if (feature.get("properties") or {}).get("WADMKK") in selected
        if (
            geometry_bounds := _geometry_bounds(feature.get("geometry") or {})
        )
        is not None
    ]
    if not bounds:
        return
    fit_map_bounds(
        earthquake_map,
        [
            [min(item[1] for item in bounds), min(item[0] for item in bounds)],
            [max(item[3] for item in bounds), max(item[2] for item in bounds)],
        ],
        padding=(26, 26),
    )


def _add_district_focus_layer(
    earthquake_map: folium.Map,
    district_geojson: dict[str, Any],
    district_names: Collection[str],
    province_names: Collection[str] | None = None,
) -> None:
    """Keep chosen districts crisp and mute every other district polygon."""
    selected = set(district_names)
    if not selected:
        return
    effective_provinces = list(province_names or [])
    if not effective_provinces:
        effective_provinces = sorted(
            {
                str((feature.get("properties") or {}).get("WADMPR") or "")
                for feature in district_geojson.get("features", [])
                if (feature.get("properties") or {}).get("WADMKK") in selected
            }
            - {""}
        )
    visible = _filter_district_features(district_geojson, effective_provinces)

    def style(feature: dict[str, Any]) -> dict[str, Any]:
        is_selected = (
            (feature.get("properties") or {}).get("WADMKK") in selected
        )
        return {
            "pane": "districtFocusPane",
            "color": "#f97316" if is_selected else "#64748b",
            "weight": 3.2 if is_selected else 0.3,
            "opacity": 1.0 if is_selected else 0.22,
            "fillColor": "#ffffff" if is_selected else "#d1d5db",
            "fillOpacity": 0.01 if is_selected else 0.46,
        }

    folium.GeoJson(
        visible,
        name="Fokus Kabupaten/Kota",
        show=True,
        pane="districtFocusPane",
        smooth_factor=1.2,
        style_function=style,
        interactive=False,
    ).add_to(earthquake_map)


def _add_district_labels(
    earthquake_map: folium.Map,
    district_geojson: dict[str, Any],
    province_names: Collection[str] | None,
    district_names: Collection[str] | None,
    font_px: int = 10,
) -> None:
    labels = folium.FeatureGroup(name="Nama Kabupaten/Kota", show=True)
    selected = set(district_names or [])
    effective_provinces = list(province_names or [])
    if selected and not effective_provinces:
        effective_provinces = sorted(
            {
                str((feature.get("properties") or {}).get("WADMPR") or "")
                for feature in district_geojson.get("features", [])
                if (feature.get("properties") or {}).get("WADMKK") in selected
            }
            - {""}
        )
    visible = _filter_district_features(district_geojson, effective_provinces)
    for feature in visible.get("features", []):
        name = str((feature.get("properties") or {}).get("WADMKK") or "").strip()
        # A district/city filter is a focus view: render only the chosen labels.
        # The surrounding polygons remain available in the muted focus layer,
        # without adding dozens of off-screen DOM markers at city-level zoom.
        if selected and name not in selected:
            continue
        ring = _largest_outer_ring(feature.get("geometry") or {})
        if not name or not ring:
            continue
        latitude, longitude = _ring_centroid(ring)
        is_selected = name in selected
        color = "#000000"
        weight = 800 if is_selected else 700
        folium.Marker(
            [latitude, longitude],
            icon=folium.DivIcon(
                class_name=(
                    "district-label district-label-selected"
                    if is_selected
                    else "district-label"
                ),
                html=(
                    f'<div style="white-space:nowrap;font:{weight} {int(font_px)}px '
                    f'Arial,sans-serif;color:{color};transform:translate(-50%,-50%);'
                    'text-shadow:-1px -1px 0 #fff,1px -1px 0 #fff,-1px 1px 0 #fff,'
                    '1px 1px 0 #fff,0 0 3px #fff;">'
                    f"{escape(name)}</div>"
                ),
            ),
            interactive=False,
            pane="districtLabelPane",
        ).add_to(labels)
    labels.add_to(earthquake_map)
    visibility = MacroElement()
    visibility._name = "DistrictLabelZoomVisibility"
    visibility._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function () {
            const map = {{ this._parent.get_name() }};
            const updateDistrictLabels = function () {
                const regionalZoom = map.getZoom() >= 8;
                map.getContainer().querySelectorAll('.district-label').forEach(function (label) {
                    const selected = label.classList.contains('district-label-selected');
                    label.style.display = (regionalZoom || selected) ? '' : 'none';
                });
            };
            map.on('zoomend layeradd', updateDistrictLabels);
            window.setTimeout(updateDistrictLabels, 0);
        })();
        {% endmacro %}
        """
    )
    visibility.add_to(earthquake_map)


def _add_shortbread_basemap(
    earthquake_map: folium.Map,
    show_roads: bool,
    show_rivers: bool,
    topographic: bool = False,
) -> str:
    map_id = earthquake_map.get_name()
    land_background = "#e6dec6" if topographic else "#e8ecd8"
    pane_setup = MacroElement()
    pane_setup._name = "ShortbreadPanes"
    pane_setup._template = Template(
        """
        {% macro script(this, kwargs) %}
        var shortbreadBasePane = {{ this._parent.get_name() }}.createPane('shortbreadBasePane');
        shortbreadBasePane.style.zIndex = 200;
        shortbreadBasePane.classList.add('shortbread-base-pane');
        var shortbreadHillshadePane = {{ this._parent.get_name() }}.createPane('shortbreadHillshadePane');
        shortbreadHillshadePane.style.zIndex = 250;
        shortbreadHillshadePane.style.pointerEvents = 'none';
        shortbreadHillshadePane.style.mixBlendMode = 'multiply';
        shortbreadHillshadePane.classList.add('shortbread-hillshade-pane');
        {% endmacro %}
        """
    )
    pane_setup.add_to(earthquake_map)
    earthquake_map.get_root().header.add_child(
        folium.Element(
            f"<style>#{map_id}{{background-color:#8fc6dd!important;}}"
            f"#{map_id} .shortbread-base-pane .leaflet-tile"
            f"{{background-color:{land_background}!important;}}"
            f"#{map_id} .shortbread-hillshade-pane .leaflet-tile"
            "{background-color:transparent!important;}</style>"
        )
    )
    if show_roads:
        road_styles = """
            street_polygons: {fill: true, fillColor: '#f8f5ef', fillOpacity: 0.9, weight: 0.4, color: '#d7d1c9'},
            streets: function(properties, zoom) {
                var major = ['motorway', 'trunk', 'primary', 'secondary'].indexOf(properties.kind) >= 0;
                return {color: major ? '#d99b72' : '#ffffff', weight: major ? 1.6 : 0.9, opacity: 0.88};
            },
            bridges: {fill: true, fillColor: '#f5f1e8', fillOpacity: 0.8, weight: 0.5, color: '#a89f95'},
        """
    else:
        road_styles = """
            street_polygons: function() { return []; },
            streets: function() { return []; },
            bridges: function() { return []; },
        """

    if show_rivers:
        river_styles = """
            water_polygons: {fill: true, fillColor: '#7ebbd6', fillOpacity: 1, weight: 0.7, color: '#579ab8'},
            water_lines: {color: '#327fa3', weight: 1.35, opacity: 0.95},
        """
    else:
        river_styles = """
            water_polygons: function(properties) {
                if (properties.kind === 'river') {
                    return [];
                }
                return {fill: true, fillColor: '#7ebbd6', fillOpacity: 1, weight: 0.7, color: '#579ab8'};
            },
            water_lines: function(properties) {
                var naturalWaterway = ['river', 'stream'].indexOf(properties.kind) >= 0;
                if (naturalWaterway) {
                    return [];
                }
                return {color: '#327fa3', weight: 1.35, opacity: 0.95};
            },
        """

    options = """{
        maxNativeZoom: 14,
        pane: 'shortbreadBasePane',
        rendererFactory: L.canvas.tile,
        interactive: false,
        updateWhenIdle: true,
        updateWhenZooming: false,
        keepBuffer: 2,
        vectorTileLayerStyles: {
            ocean: {fill: true, fillColor: '#8fc6dd', fillOpacity: 1, weight: 0.5, color: '#66a9c5'},
            __RIVER_STYLES__
            water_polygons_labels: function() { return []; },
            water_lines_labels: function() { return []; },
            dam_lines: {color: '#7d8b91', weight: 1, opacity: 0.8},
            dam_polygons: {fill: true, fillColor: '#aeb8b9', fillOpacity: 0.75, weight: 0.5, color: '#7d8b91'},
            land: function(properties) {
                var colors = {
                    forest: '#b7d39f', wood: '#b7d39f', grass: '#d0e1b6',
                    farmland: '#e8dfa9', farmyard: '#dfd4a5',
                    residential: '#e9e6dc', industrial: '#d8d5c9',
                    cemetery: '#c9dcb7', sand: '#eee0b6', beach: '#f2dfa8'
                };
                return {fill: true, fillColor: colors[properties.kind] || '#e8ecd8', fillOpacity: 1, weight: 0};
            },
            sites: function(properties, zoom) {
                return zoom < 11 ? [] : {fill: true, fillColor: '#e4dfd3', fillOpacity: 0.62, weight: 0};
            },
            buildings: function(properties, zoom) {
                return zoom < 13 ? [] : {fill: true, fillColor: '#d4cbc0', fillOpacity: 0.75, weight: 0.4, color: '#b8aca0'};
            },
            addresses: function() { return []; },
            __ROAD_STYLES__
            boundaries: function(properties) {
                var national = Number(properties.admin_level || 9) <= 2;
                return {color: national ? '#4e5965' : '#7b8792', weight: national ? 1.8 : 1.15, opacity: 0.9, dashArray: national ? null : '5 4'};
            },
            boundary_labels: function() { return []; },
            place_labels: function() { return []; },
            street_labels: function() { return []; },
            streets_polygons_labels: function() { return []; },
            street_labels_points: function() { return []; },
            public_transport: function() { return []; },
            pois: function() { return []; },
            ferries: {color: '#4d9cb8', weight: 1.1, opacity: 0.8, dashArray: '5 5'},
            aerialways: {color: '#8a7f78', weight: 0.8, opacity: 0.7},
            pier_lines: {color: '#87929b', weight: 1.2, opacity: 0.8},
            pier_polygons: {fill: true, fillColor: '#d9d6ce', fillOpacity: 0.8, weight: 0.5, color: '#87929b'}
        }
    }""".replace("__ROAD_STYLES__", road_styles).replace(
        "__RIVER_STYLES__",
        river_styles,
    )
    if topographic:
        options = (
            options.replace(
                "forest: '#b7d39f', wood: '#b7d39f', grass: '#d0e1b6'",
                "forest: '#a7c689', wood: '#a7c689', grass: '#c4d69d'",
            )
            .replace(
                "farmland: '#e8dfa9', farmyard: '#dfd4a5'",
                "farmland: '#ddd0a0', farmyard: '#d3c79a'",
            )
            .replace(
                "colors[properties.kind] || '#e8ecd8'",
                "colors[properties.kind] || '#e6dec6'",
            )
        )
    vector_layer = VectorGridProtobuf(
        "https://tiles.versatiles.org/tiles/osm/{z}/{x}/{y}",
        name=BASEMAP_SHORTBREAD,
        options=options,
        overlay=False,
        control=True,
        show=True,
    )
    vector_layer.default_js = [
        (
            "vectorGrid",
            "https://unpkg.com/leaflet.vectorgrid@1.3.0/"
            "dist/Leaflet.VectorGrid.bundled.js",
        )
    ]
    vector_layer.add_to(earthquake_map)
    return BASEMAP_SHORTBREAD


def _add_shortbread_topography(
    earthquake_map: folium.Map,
    opacity: float = 0.5,
) -> None:
    folium.TileLayer(
        tiles=(
            "https://services.arcgisonline.com/arcgis/rest/services/"
            "Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}"
        ),
        attr=HILLSHADE_ATTRIBUTION,
        name="Topografi (Hillshade)",
        overlay=True,
        control=False,
        show=True,
        pane="shortbreadHillshadePane",
        opacity=opacity,
        max_native_zoom=23,
        max_zoom=23,
        keep_buffer=0,
        update_when_idle=True,
        update_when_zooming=False,
        cross_origin=True,
    ).add_to(earthquake_map)


def _add_relief_basemap(earthquake_map: folium.Map) -> str:
    """Detailed colored Esri topographic tiles for district-scale viewing."""
    map_id = earthquake_map.get_name()
    earthquake_map.get_root().header.add_child(
        folium.Element(f"<style>#{map_id}{{background-color:#a9c7d6!important;}}</style>")
    )
    folium.TileLayer(
        tiles=(
            "https://services.arcgisonline.com/ArcGIS/rest/services/"
            "World_Topo_Map/MapServer/tile/{z}/{y}/{x}"
        ),
        attr=RELIEF_ATTRIBUTION,
        name=BASEMAP_RELIEF,
        max_native_zoom=19,
        max_zoom=19,
        control=True,
        keep_buffer=1,
        update_when_idle=True,
        update_when_zooming=False,
        cross_origin=True,
    ).add_to(earthquake_map)
    return BASEMAP_RELIEF


def _add_distance_measure_control(earthquake_map: folium.Map) -> None:
    """Add a single-use click-and-drag geodesic distance ruler."""
    earthquake_map.get_root().header.add_child(
        folium.Element(
            """
            <style>
            .distance-measure-control{margin-top:10px!important;}
            .map-legend-density{top:108px!important;}
            .distance-measure-control a{width:34px!important;height:34px!important;
              line-height:34px!important;display:flex!important;align-items:center;
              justify-content:center;background:#fff;color:#111827!important;
              cursor:pointer;text-decoration:none!important;}
            .distance-measure-control a:hover{background:#f3f4f6;}
            .distance-measure-control a.is-active{background:#dbeafe;color:#1d4ed8!important;}
            .distance-measure-icon{width:21px;height:21px;display:block;}
            .distance-measure-endpoint{width:10px!important;height:10px!important;
              margin:0!important;background:#fff;border:2px solid #1d4ed8;
              border-radius:50%;box-sizing:border-box;}
            .leaflet-container.distance-measure-active{cursor:crosshair!important;}
            .distance-measure-popup .leaflet-popup-content{margin:8px 12px;
              font:600 13px/1.35 Arial,sans-serif;white-space:nowrap;color:#111827;}
            </style>
            """
        )
    )

    distance_control = MacroElement()
    distance_control._name = "DistanceMeasureControl"
    distance_control._template = Template(
        """
        {% macro script(this, kwargs) %}
        (function () {
            const map = {{ this._parent.get_name() }};
            const mapContainer = map.getContainer();
            let active = false;
            let drawing = false;
            let activePointerId = null;
            let startLatLng = null;
            let startPoint = null;
            let measureLine = null;
            let startMarker = null;
            let endMarker = null;
            let distancePopup = null;
            let controlButton = null;
            let draggingWasEnabled = false;
            let suppressNextMapClick = false;

            const removeLayer = function (layer) {
                if (layer && map.hasLayer(layer)) {
                    map.removeLayer(layer);
                }
            };

            const clearMeasurement = function () {
                const popupToRemove = distancePopup;
                distancePopup = null;
                removeLayer(measureLine);
                removeLayer(startMarker);
                removeLayer(endMarker);
                removeLayer(popupToRemove);
                measureLine = null;
                startMarker = null;
                endMarker = null;
                startLatLng = null;
                startPoint = null;
            };

            const deactivate = function () {
                active = false;
                drawing = false;
                activePointerId = null;
                mapContainer.classList.remove("distance-measure-active");
                if (controlButton) {
                    controlButton.classList.remove("is-active");
                    controlButton.setAttribute("aria-pressed", "false");
                }
                if (draggingWasEnabled && map.dragging) {
                    map.dragging.enable();
                }
                draggingWasEnabled = false;
            };

            const activate = function () {
                active = true;
                draggingWasEnabled = Boolean(map.dragging && map.dragging.enabled());
                if (draggingWasEnabled) {
                    map.dragging.disable();
                }
                mapContainer.classList.add("distance-measure-active");
                controlButton.classList.add("is-active");
                controlButton.setAttribute("aria-pressed", "true");
            };

            const eventPoint = function (event) {
                return map.mouseEventToContainerPoint(event);
            };

            const stopPointerEvent = function (event) {
                event.preventDefault();
                event.stopPropagation();
            };

            const onPointerDown = function (event) {
                if (!active || event.button !== 0) {
                    return;
                }
                stopPointerEvent(event);
                clearMeasurement();
                drawing = true;
                activePointerId = event.pointerId;
                startPoint = eventPoint(event);
                startLatLng = map.containerPointToLatLng(startPoint);
                try {
                    mapContainer.setPointerCapture(activePointerId);
                } catch (_error) {
                    // Pointer capture is optional; document-level movement is not required.
                }
                measureLine = L.polyline([startLatLng, startLatLng], {
                    color: "#2563eb",
                    weight: 3,
                    opacity: 0.95,
                    interactive: false,
                }).addTo(map);
                const endpointIcon = L.divIcon({
                    className: "distance-measure-endpoint",
                    iconSize: [10, 10],
                    iconAnchor: [5, 5],
                });
                startMarker = L.marker(startLatLng, {
                    icon: endpointIcon,
                    interactive: false,
                    keyboard: false,
                }).addTo(map);
                endMarker = L.marker(startLatLng, {
                    icon: endpointIcon,
                    interactive: false,
                    keyboard: false,
                }).addTo(map);
            };

            const onPointerMove = function (event) {
                if (!active || !drawing || event.pointerId !== activePointerId) {
                    return;
                }
                stopPointerEvent(event);
                const currentLatLng = map.containerPointToLatLng(eventPoint(event));
                measureLine.setLatLngs([startLatLng, currentLatLng]);
                endMarker.setLatLng(currentLatLng);
            };

            const cancelDrawing = function () {
                if (drawing) {
                    clearMeasurement();
                }
                deactivate();
            };

            const onPointerUp = function (event) {
                if (!active || !drawing || event.pointerId !== activePointerId) {
                    return;
                }
                stopPointerEvent(event);
                const endPoint = eventPoint(event);
                const endLatLng = map.containerPointToLatLng(endPoint);
                const pixelDistance = startPoint.distanceTo(endPoint);
                suppressNextMapClick = true;
                try {
                    mapContainer.releasePointerCapture(activePointerId);
                } catch (_error) {
                    // The pointer may already have been released by the browser.
                }
                if (pixelDistance < 5) {
                    cancelDrawing();
                    return;
                }

                measureLine.setLatLngs([startLatLng, endLatLng]);
                endMarker.setLatLng(endLatLng);
                const distanceKm = map.distance(startLatLng, endLatLng) / 1000;
                const formattedDistance = distanceKm.toLocaleString("id-ID", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                });
                map.closePopup();
                distancePopup = L.popup({
                    className: "distance-measure-popup",
                    closeButton: true,
                    autoClose: false,
                    closeOnClick: false,
                    offset: [0, -5],
                })
                    .setLatLng(endLatLng)
                    .setContent("<strong>Jarak:</strong> " + formattedDistance + " km")
                    .openOn(map);
                deactivate();
            };

            const onMapClick = function (event) {
                const clickedControl = controlButton && (
                    event.target === controlButton || controlButton.contains(event.target)
                );
                if (clickedControl) {
                    return;
                }
                if (active || suppressNextMapClick) {
                    stopPointerEvent(event);
                    suppressNextMapClick = false;
                }
            };

            mapContainer.addEventListener("pointerdown", onPointerDown, true);
            mapContainer.addEventListener("pointermove", onPointerMove, true);
            mapContainer.addEventListener("pointerup", onPointerUp, true);
            mapContainer.addEventListener("pointercancel", cancelDrawing, true);
            mapContainer.addEventListener("click", onMapClick, true);
            map.on("popupclose", function (event) {
                if (event.popup === distancePopup) {
                    clearMeasurement();
                }
            });
            document.addEventListener("keydown", function (event) {
                if (active && event.key === "Escape") {
                    cancelDrawing();
                }
            });

            const DistanceMeasureControl = L.Control.extend({
                options: {position: "topright"},
                onAdd: function () {
                    const container = L.DomUtil.create(
                        "div",
                        "leaflet-bar distance-measure-control"
                    );
                    controlButton = L.DomUtil.create(
                        "a",
                        "distance-measure-button",
                        container
                    );
                    controlButton.href = "#";
                    controlButton.setAttribute("role", "button");
                    controlButton.setAttribute("title", "Ukur jarak");
                    controlButton.setAttribute("aria-label", "Ukur jarak");
                    controlButton.setAttribute("aria-pressed", "false");
                    controlButton.innerHTML =
                        '<svg class="distance-measure-icon" viewBox="0 0 24 24" ' +
                        'aria-hidden="true" focusable="false">' +
                        '<path d="M4.7 16.5 16.5 4.7l2.8 2.8L7.5 19.3z" fill="none" ' +
                        'stroke="currentColor" stroke-width="2" stroke-linejoin="round"/>' +
                        '<path d="m8.1 13.1 1.4 1.4m1.2-4.2 1.4 1.4m1.2-4.2 1.4 1.4" ' +
                        'stroke="currentColor" stroke-width="1.5"/></svg>';
                    L.DomEvent.disableClickPropagation(container);
                    L.DomEvent.disableScrollPropagation(container);
                    L.DomEvent.on(controlButton, "click", function (event) {
                        L.DomEvent.stop(event);
                        if (active) {
                            cancelDrawing();
                        } else {
                            activate();
                        }
                    });
                    return container;
                },
            });

            const control = new DistanceMeasureControl().addTo(map);
            const controlContainer = control.getContainer();
            const controlCorner = controlContainer.parentNode;
            const placeBelowLayerControl = function () {
                const layerControl = controlCorner.querySelector(".leaflet-control-layers");
                if (layerControl && layerControl.nextSibling !== controlContainer) {
                    controlCorner.insertBefore(controlContainer, layerControl.nextSibling);
                }
                const densityLegend = document.querySelector(".map-legend-density");
                if (densityLegend) {
                    const rulerBounds = controlContainer.getBoundingClientRect();
                    densityLegend.style.setProperty(
                        "top",
                        Math.ceil(rulerBounds.bottom + 8) + "px",
                        "important"
                    );
                }
            };
            const controlObserver = new MutationObserver(placeBelowLayerControl);
            controlObserver.observe(controlCorner, {childList: true});
            window.setTimeout(placeBelowLayerControl, 0);
            window.addEventListener("load", placeBelowLayerControl, {once: true});
        })();
        {% endmacro %}
        """
    )
    distance_control.add_to(earthquake_map)


def add_configured_basemap(
    earthquake_map: folium.Map,
    basemap_name: str,
    show_shortbread_roads: bool,
    show_shortbread_rivers: bool,
    show_shortbread_topography: bool,
) -> str:
    """Add one of the shared application basemaps to a Folium map."""
    if basemap_name == BASEMAP_RELIEF:
        return _add_relief_basemap(earthquake_map)

    if basemap_name == BASEMAP_CYCLOSM:
        folium.TileLayer(
            tiles="https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
            attr=CYCLOSM_ATTRIBUTION,
            name=BASEMAP_CYCLOSM,
            max_zoom=20,
            control=True,
        ).add_to(earthquake_map)
        return BASEMAP_CYCLOSM

    if basemap_name == BASEMAP_HUMANITARIAN:
        folium.TileLayer(
            tiles="https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
            attr=HUMANITARIAN_ATTRIBUTION,
            name=BASEMAP_HUMANITARIAN,
            max_zoom=20,
            control=True,
        ).add_to(earthquake_map)
        return BASEMAP_HUMANITARIAN

    if basemap_name == BASEMAP_SHORTBREAD:
        selected_basemap = _add_shortbread_basemap(
            earthquake_map,
            show_shortbread_roads,
            show_shortbread_rivers,
            topographic=show_shortbread_topography,
        )
        if show_shortbread_topography:
            _add_shortbread_topography(earthquake_map)
        return selected_basemap

    folium.TileLayer(
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr=OPENTOPO_ATTRIBUTION,
        name=BASEMAP_OPENTOPO,
        max_zoom=17,
        control=True,
    ).add_to(earthquake_map)
    return BASEMAP_OPENTOPO


def build_indonesia_map(
    df: pd.DataFrame | None = None,
    fault_geojson: dict[str, Any] | None = None,
    volcano_geojson: dict[str, Any] | None = None,
    trench_geojson: dict[str, Any] | None = None,
    slab_geojson: dict[str, Any] | None = None,
    show_basemap: bool = True,
    show_earthquakes: bool = True,
    show_faults: bool = True,
    show_volcanoes: bool = False,
    show_fault_labels: bool = True,
    show_trenches: bool = True,
    show_slabs: bool = True,
    show_stations: bool = False,
    station_records: Collection[tuple[Any, ...]] | None = None,
    fault_names: Collection[str] | None = None,
    fault_feature_indexes: Collection[int] | None = None,
    volcano_names: Collection[str] | None = None,
    province_geojson: dict[str, Any] | None = None,
    district_geojson: dict[str, Any] | None = None,
    population_density_geojson: dict[str, Any] | None = None,
    show_population_density: bool = False,
    show_population_density_legend: bool = True,
    population_density_mode: str | None = None,
    show_provinces: bool = False,
    province_names: Collection[str] | None = None,
    district_names: Collection[str] | None = None,
    show_region_labels: bool = True,
    region_label_font_px: int = 11,
    basemap_name: str = BASEMAP_OPENTOPO,
    show_shortbread_roads: bool = False,
    show_shortbread_rivers: bool = True,
    show_shortbread_topography: bool = False,
    show_earthquake_legend: bool = True,
    show_tectonic_legend: bool = True,
    show_layer_control: bool = True,
    show_map_scale: bool = True,
    show_distance_measure: bool = True,
    include_geology_layer: bool = True,
    geology_initially_visible: bool = False,
    geology_units: Collection[dict[str, Any]] | None = None,
    geology_selected_keys: Collection[str] | None = None,
    geology_style_mode: str = GEOLOGY_STYLE_OFFICIAL,
    zoom_snap: float = 1.0,
    earthquake_marker_scale: float = 1.0,
    embed_earthquake_layer: bool = True,
) -> folium.Map:
    """Build an Indonesia map with optional earthquake and tectonic layers."""
    if df is not None and not df.empty:
        center = (float(df["lintang"].mean()), float(df["bujur"].mean()))
        zoom = DEFAULT_ZOOM
    else:
        center = DEFAULT_CENTER
        zoom = DEFAULT_ZOOM

    earthquake_map = folium.Map(
        location=center,
        zoom_start=zoom,
        control_scale=show_map_scale,
        tiles=None,
        zoom_snap=zoom_snap,
    )
    _add_thematic_panes(earthquake_map)
    _add_collapsible_legend_styles(earthquake_map)
    if include_geology_layer:
        geology_region_filter, geology_region_label = build_geology_region_filter(
            province_geojson=province_geojson,
            district_geojson=district_geojson,
            province_names=province_names,
            district_names=district_names,
        )
        add_dynamic_geology_layer(
            earthquake_map,
            region_filter_geojson=geology_region_filter,
            region_filter_label=geology_region_label,
            initially_visible=geology_initially_visible,
            geology_units=geology_units,
            selected_formation_keys=geology_selected_keys,
            show_full_formation_names=bool(district_names),
            style_mode=geology_style_mode,
        )
    if show_distance_measure:
        _add_distance_measure_control(earthquake_map)
    volcano_bounds = (
        _volcano_area_bounds(volcano_geojson, volcano_names)
        if volcano_geojson is not None and show_volcanoes
        else None
    )
    if volcano_bounds is not None:
        fit_map_bounds(earthquake_map, volcano_bounds, padding=(28, 28))
    elif district_geojson is not None and district_names:
        _fit_selected_districts(
            earthquake_map,
            district_geojson,
            district_names,
        )
    elif df is not None and not df.empty:
        fit_map_bounds(
            earthquake_map,
            [
                [float(df["lintang"].min()), float(df["bujur"].min())],
                [float(df["lintang"].max()), float(df["bujur"].max())],
            ],
            padding=(24, 24),
        )
    elif province_geojson is not None and province_names:
        _fit_selected_provinces(
            earthquake_map,
            province_geojson,
            province_names,
        )
    if show_basemap:
        add_configured_basemap(
            earthquake_map,
            basemap_name,
            show_shortbread_roads,
            show_shortbread_rivers,
            show_shortbread_topography,
        )
    if show_earthquake_legend and show_earthquakes and df is not None and not df.empty:
        _add_earthquake_legend(earthquake_map, earthquake_marker_scale)

    if province_geojson is not None and show_provinces:
        visible_provinces = _filter_province_features(
            province_geojson,
            province_names,
        )
        folium.GeoJson(
            visible_provinces,
            name="Batas Provinsi",
            style_function=lambda _feature: {
                "color": "#047857",
                "fillColor": "#10b981",
                "fillOpacity": 0.04,
                "weight": 1.8,
                "opacity": 0.9,
            },
            highlight_function=lambda _feature: {
                "color": "#064e3b",
                "fillOpacity": 0.12,
                "weight": 3,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["PROVINSI"],
                aliases=["Provinsi"],
                sticky=False,
            ),
        ).add_to(earthquake_map)

    if province_geojson is not None and show_region_labels:
        _add_province_labels(
            earthquake_map,
            province_geojson,
            province_names,
            font_px=region_label_font_px,
        )

    if (
        district_geojson is not None
        and show_region_labels
        and (
            basemap_name == BASEMAP_RELIEF
            or (basemap_name == BASEMAP_SHORTBREAD and show_shortbread_topography)
            or bool(district_names)
        )
    ):
        _add_district_labels(
            earthquake_map,
            district_geojson,
            province_names,
            district_names,
            font_px=max(9, region_label_font_px - 1),
        )

    if population_density_geojson is not None and show_population_density:
        add_population_density_layer(
            earthquake_map,
            population_density_geojson,
            province_geojson=province_geojson,
            show_legend=show_population_density_legend,
            mode=(
                population_density_mode
                or ("district" if district_names else "province")
            ),
        )

    if district_geojson is not None and district_names:
        _add_district_focus_layer(
            earthquake_map,
            district_geojson,
            district_names,
            province_names,
        )

    has_visible_faults = False
    if fault_geojson is not None and show_faults:
        visible_faults = _filter_fault_features(
            fault_geojson,
            fault_names,
            fault_feature_indexes,
        )
        has_visible_faults = bool(visible_faults.get("features"))
        if has_visible_faults:
            fault_layer = folium.FeatureGroup(name="Sesar PUSGEN 2024", show=True)
            folium.GeoJson(
                visible_faults,
                style_function=_fault_style,
                highlight_function=lambda _feature: {
                    "color": "#6d123d",
                    "weight": 4.5,
                    "opacity": 1,
                },
                tooltip=folium.GeoJsonTooltip(
                    fields=["Name", "Segment", "Region", "Type", "Mmax", "Sliprate_m"],
                    aliases=["Sesar", "Segmen", "Wilayah", "Tipe", "Mmax", "Slip rate"],
                    localize=True,
                    sticky=False,
                ),
            ).add_to(fault_layer)
            _add_fault_symbol_canvas_layer(fault_layer, visible_faults)
            if show_fault_labels:
                _add_fault_labels(fault_layer, visible_faults, earthquake_map)
            fault_layer.add_to(earthquake_map)

    if volcano_geojson is not None and show_volcanoes:
        _add_volcano_layer(
            earthquake_map,
            volcano_geojson,
            volcano_names,
        )

    if trench_geojson is not None and show_trenches:
        _add_trench_layer(earthquake_map, trench_geojson)

    has_visible_slabs = bool(
        slab_geojson is not None
        and show_slabs
        and slab_geojson.get("features")
    )
    if has_visible_slabs:
        _add_slab_layer(earthquake_map, slab_geojson)

    if station_records:
        _add_station_canvas_layer(
            earthquake_map,
            station_records,
            show=show_stations,
        )

    if show_tectonic_legend:
        _add_tectonic_legend(
            earthquake_map,
            show_faults=has_visible_faults,
            show_trenches=trench_geojson is not None and show_trenches,
            show_slabs=has_visible_slabs,
        )

    if df is None or df.empty:
        if show_layer_control:
            folium.LayerControl(collapsed=True).add_to(earthquake_map)
        return earthquake_map

    if show_earthquakes and embed_earthquake_layer:
        _add_earthquake_canvas_layer(
            earthquake_map,
            df,
            earthquake_marker_scale,
        )

    if show_layer_control:
        folium.LayerControl(collapsed=True).add_to(earthquake_map)

    return earthquake_map
