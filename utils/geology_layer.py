from __future__ import annotations

import json
import html
from collections.abc import Collection
from typing import Any

import folium
from branca.element import MacroElement, Template

from utils.geology_catalog import (
    CGMW_2022_SOURCE_URL,
    GEOLOGY_STYLE_CGMW,
    GEOLOGY_STYLE_OFFICIAL,
    GeologyUnit,
    build_dynamic_layers,
    build_formation_definition,
    geology_age_legend,
    get_geology_age_style,
)


GEOLOGY_LAYER_NAME = "Geologi Indonesia (PSG/BIG-KSP)"
GEOLOGY_MAPSERVER_URL = (
    "https://kspservices.big.go.id/satupeta/rest/services/"
    "PUBLIK/SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer"
)
GEOLOGY_SOURCE_URL = f"{GEOLOGY_MAPSERVER_URL}/8"
GEOLOGY_ATTRIBUTION = (
    "Peta Geologi: Pusat Survei Geologi, Badan Geologi, KESDM "
    "via Kebijakan Satu Peta/BIG"
)

GEOLOGY_AGE_LEGEND: tuple[tuple[str, str], ...] = geology_age_legend()


def _rounded_coordinates(value: Any) -> Any:
    if isinstance(value, list):
        return [_rounded_coordinates(item) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(float(value), 5)
    return value


def build_geology_region_filter(
    *,
    province_geojson: dict[str, Any] | None,
    district_geojson: dict[str, Any] | None,
    province_names: Collection[str] | None,
    district_names: Collection[str] | None,
) -> tuple[dict[str, Any] | None, str]:
    """Build a compact geometry-only filter from the active admin selection."""
    selected_districts = {str(name) for name in (district_names or []) if str(name)}
    selected_provinces = {str(name) for name in (province_names or []) if str(name)}
    if selected_districts:
        source = district_geojson or {}
        property_name = "WADMKK"
        selected_names = selected_districts
        label_prefix = "Kabupaten/Kota"
    elif selected_provinces:
        source = province_geojson or {}
        property_name = "PROVINSI"
        selected_names = selected_provinces
        label_prefix = "Provinsi"
    else:
        return None, "Seluruh Indonesia"

    features: list[dict[str, Any]] = []
    for feature in source.get("features", []):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        if properties.get(property_name) not in selected_names:
            continue
        if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        if not isinstance(geometry.get("coordinates"), list):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": geometry["type"],
                    "coordinates": _rounded_coordinates(geometry["coordinates"]),
                },
            }
        )

    label = f"{label_prefix}: {', '.join(sorted(selected_names))}"
    return {"type": "FeatureCollection", "features": features}, label


def _legend_rows_html(style_mode: str = GEOLOGY_STYLE_OFFICIAL) -> str:
    return "".join(
        (
            '<div class="geology-legend-row">'
            f'<span style="background:{color}"></span>{label}</div>'
        )
        for label, color in geology_age_legend(style_mode)
    )


def _coerce_units(values: Collection[GeologyUnit | dict[str, Any]] | None) -> tuple[GeologyUnit, ...]:
    units: list[GeologyUnit] = []
    for value in values or []:
        if isinstance(value, GeologyUnit):
            units.append(value)
        else:
            units.append(
                GeologyUnit(
                    key=str(value.get("key") or ""),
                    symbol=str(value.get("symbol") or value.get("simobj") or ""),
                    name_en=str(value.get("name_en") or value.get("namobj") or ""),
                    age=str(value.get("age") or value.get("umurobj") or ""),
                    name_id=str(value.get("name_id") or value.get("remark") or ""),
                    feature_count=int(value.get("feature_count") or 0),
                )
            )
    return tuple(units)


def _formation_rows_html(
    units: Collection[GeologyUnit],
    selected_keys: Collection[str],
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
) -> str:
    selected = set(selected_keys)
    visible = [unit for unit in units if not selected or unit.key in selected]
    if not visible:
        return ""
    return "".join(
        (
            '<div class="geology-formation-row">'
            f'<span style="background:{get_geology_age_style(unit.age, style_mode).hex}"></span>'
            f'<div><strong>{html.escape(unit.display_name)}</strong>'
            f'<small><b>Umur:</b> {html.escape(unit.age or "tidak tersedia")}. '
            f'<b>Unit/formasi:</b> {html.escape(unit.name_id or unit.name_en or "tanpa nama")}.'
            f'</small></div></div>'
        )
        for unit in visible
    )


def add_dynamic_geology_layer(
    earthquake_map: folium.Map,
    *,
    region_filter_geojson: dict[str, Any] | None = None,
    region_filter_label: str = "Seluruh Indonesia",
    initially_visible: bool = False,
    geology_units: Collection[GeologyUnit | dict[str, Any]] | None = None,
    selected_formation_keys: Collection[str] | None = None,
    show_full_formation_names: bool = False,
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
) -> folium.FeatureGroup:
    """Add official Layer 8 as a viewport-driven ArcGIS image overlay."""
    units = _coerce_units(geology_units)
    selected_keys = tuple(str(key) for key in (selected_formation_keys or []))
    definition_expression = build_formation_definition(units, selected_keys)
    dynamic_layers = build_dynamic_layers(
        units,
        selected_keys,
        show_formation_labels=True,
        formation_label_expression=(
            "[remark]" if show_full_formation_names or selected_keys else "[simobj]"
        ),
        style_mode=style_mode,
    )
    formation_rows = _formation_rows_html(units, selected_keys, style_mode)
    style_source = (
        f'<a href="{CGMW_2022_SOURCE_URL}" target="_blank" '
        'rel="noopener noreferrer">CGMW 2022</a>'
        if style_mode == GEOLOGY_STYLE_CGMW
        else "renderer resmi BIG/KSP Layer 8"
    )
    geology_layer = folium.FeatureGroup(
        name=GEOLOGY_LAYER_NAME,
        overlay=True,
        control=True,
        show=initially_visible,
    )
    geology_layer.add_to(earthquake_map)

    earthquake_map.get_root().html.add_child(
        folium.Element(
            f"""
            <details class="map-legend map-legend-geology" hidden>
              <summary>Legenda Geologi</summary>
              <div class="map-legend-body">
                <div class="map-legend-subtitle">Mode warna: {html.escape(style_mode)}</div>
                <div class="geology-legend-grid">{_legend_rows_html(style_mode)}</div>
                <div class="geology-legend-note"><strong>Warna menunjukkan umur batuan.</strong>
                  Nama/sandi menunjukkan unit atau formasi.
                  Formasi berbeda dengan umur sama menggunakan warna yang sama.</div>
                <div>Filter: <span class="geology-filter-label"></span></div>
                <div>Klik peta untuk melihat nama formasi dan umur geologi.</div>
                {('<details class="geology-formation-index"><summary>Unit/formasi dalam filter</summary>' + formation_rows + '</details>') if formation_rows else ''}
                <div class="geology-legend-status">Layer nonaktif.</div>
                <div class="geology-legend-source">
                  Sumber: <a href="{GEOLOGY_SOURCE_URL}" target="_blank"
                  rel="noopener noreferrer">Pusat Survei Geologi, Badan Geologi,
                  KESDM</a>; disajikan melalui Kebijakan Satu Peta/BIG.
                  Warna: {style_source}.
                </div>
              </div>
            </details>
            """
        )
    )

    dynamic_layer = MacroElement()
    dynamic_layer._name = "DynamicGeologyLayer"
    dynamic_layer.layer_name = geology_layer.get_name()
    dynamic_layer.service_url = json.dumps(GEOLOGY_MAPSERVER_URL)
    dynamic_layer.attribution = json.dumps(GEOLOGY_ATTRIBUTION)
    dynamic_layer.filter_geojson = json.dumps(
        region_filter_geojson,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    dynamic_layer.filter_label = json.dumps(region_filter_label, ensure_ascii=False)
    dynamic_layer.initially_visible = "true" if initially_visible else "false"
    dynamic_layer.dynamic_layers = json.dumps(dynamic_layers, ensure_ascii=False)
    dynamic_layer.definition_expression = json.dumps(definition_expression, ensure_ascii=False)
    dynamic_layer._template = Template(
        r"""
        {% macro script(this, kwargs) %}
        (function () {
            const map = {{ this._parent.get_name() }};
            const geologyLayer = {{ this.layer_name }};
            const geologyServiceUrl = {{ this.service_url }};
            const geologyAttribution = {{ this.attribution }};
            const geologyFilterGeoJson = {{ this.filter_geojson }};
            const geologyFilterLabel = {{ this.filter_label }};
            const geologyLayerInitiallyVisible = {{ this.initially_visible }};
            const geologyDynamicLayers = {{ this.dynamic_layers }};
            const geologyDefinitionExpression = {{ this.definition_expression }};
            const legend = document.querySelector(".map-legend-geology");
            const legendStatus = legend
                ? legend.querySelector(".geology-legend-status")
                : null;
            const legendFilterLabel = legend
                ? legend.querySelector(".geology-filter-label")
                : null;
            const geologyFilterLayer = geologyFilterGeoJson
                ? L.geoJSON(geologyFilterGeoJson, {interactive: false})
                : null;
            const geologyFilterBounds = geologyFilterLayer
                ? geologyFilterLayer.getBounds()
                : null;
            let geologyImageOverlay = null;
            let geologyImageObjectUrl = null;
            let exportController = null;
            let identifyController = null;
            let refreshTimer = null;
            let requestSequence = 0;
            let attributionVisible = false;
            let lastExportKey = null;

            if (legendFilterLabel) {
                legendFilterLabel.textContent = geologyFilterLabel;
            }

            const setStatus = function (message) {
                if (legendStatus) {
                    legendStatus.textContent = message;
                }
            };

            const setLegendVisible = function (visible) {
                if (!legend) {
                    return;
                }
                legend.hidden = !visible;
                legend.style.display = visible ? "block" : "none";
            };

            const setAttributionVisible = function (visible) {
                if (visible && !attributionVisible) {
                    map.attributionControl.addAttribution(geologyAttribution);
                    attributionVisible = true;
                } else if (!visible && attributionVisible) {
                    map.attributionControl.removeAttribution(geologyAttribution);
                    attributionVisible = false;
                }
            };

            const escapeGeologyHtml = function (value) {
                const container = document.createElement("div");
                container.textContent = String(value == null ? "" : value);
                return container.innerHTML;
            };

            const getVisibleGeologyBounds = function () {
                const viewportBounds = map.getBounds();
                if (!geologyFilterGeoJson) {
                    return viewportBounds;
                }
                if (
                    !geologyFilterBounds ||
                    !geologyFilterBounds.isValid() ||
                    !viewportBounds.overlaps(geologyFilterBounds)
                ) {
                    return null;
                }
                const south = Math.max(
                    viewportBounds.getSouth(), geologyFilterBounds.getSouth()
                );
                const west = Math.max(
                    viewportBounds.getWest(), geologyFilterBounds.getWest()
                );
                const north = Math.min(
                    viewportBounds.getNorth(), geologyFilterBounds.getNorth()
                );
                const east = Math.min(
                    viewportBounds.getEast(), geologyFilterBounds.getEast()
                );
                if (south >= north || west >= east) {
                    return null;
                }
                return L.latLngBounds([south, west], [north, east]);
            };

            const pointInRing = function (longitude, latitude, ring) {
                let inside = false;
                for (let index = 0, previous = ring.length - 1;
                    index < ring.length;
                    previous = index, index += 1) {
                    const currentPoint = ring[index];
                    const previousPoint = ring[previous];
                    if (!Array.isArray(currentPoint) || !Array.isArray(previousPoint)) {
                        continue;
                    }
                    const currentX = Number(currentPoint[0]);
                    const currentY = Number(currentPoint[1]);
                    const previousX = Number(previousPoint[0]);
                    const previousY = Number(previousPoint[1]);
                    const crosses = ((currentY > latitude) !== (previousY > latitude)) &&
                        (longitude < (previousX - currentX) *
                        (latitude - currentY) / (previousY - currentY) + currentX);
                    if (crosses) {
                        inside = !inside;
                    }
                }
                return inside;
            };

            const pointInPolygon = function (longitude, latitude, rings) {
                if (!Array.isArray(rings) || !rings.length) {
                    return false;
                }
                if (!pointInRing(longitude, latitude, rings[0])) {
                    return false;
                }
                return !rings.slice(1).some(function (hole) {
                    return pointInRing(longitude, latitude, hole);
                });
            };

            const geometryContainsPoint = function (geometry, latlng) {
                if (!geometry || !latlng) {
                    return false;
                }
                if (geometry.type === "Polygon") {
                    return pointInPolygon(latlng.lng, latlng.lat, geometry.coordinates);
                }
                if (geometry.type === "MultiPolygon") {
                    return geometry.coordinates.some(function (polygon) {
                        return pointInPolygon(latlng.lng, latlng.lat, polygon);
                    });
                }
                return false;
            };

            const pointInGeologyFilter = function (latlng) {
                if (!geologyFilterGeoJson) {
                    return true;
                }
                return geologyFilterGeoJson.features.some(function (feature) {
                    return geometryContainsPoint(feature && feature.geometry, latlng);
                });
            };

            const extentInfoForBounds = function (bounds) {
                const southWest = L.CRS.EPSG3857.project(bounds.getSouthWest());
                const northEast = L.CRS.EPSG3857.project(bounds.getNorthEast());
                const projectedExtent = [
                    southWest.x, southWest.y, northEast.x, northEast.y,
                ];
                return {
                    bounds: bounds,
                    projectedExtent: projectedExtent,
                };
            };

            const geometryRings = function (geometry) {
                if (!geometry || !Array.isArray(geometry.coordinates)) {
                    return [];
                }
                if (geometry.type === "Polygon") {
                    return geometry.coordinates;
                }
                if (geometry.type === "MultiPolygon") {
                    return geometry.coordinates.reduce(function (rings, polygon) {
                        return rings.concat(polygon);
                    }, []);
                }
                return [];
            };

            const geologyClipPath = function (projectedExtent) {
                if (!geologyFilterGeoJson) {
                    return "";
                }
                const xmin = projectedExtent[0];
                const ymin = projectedExtent[1];
                const xmax = projectedExtent[2];
                const ymax = projectedExtent[3];
                const width = xmax - xmin;
                const height = ymax - ymin;
                const pathParts = [];
                geologyFilterGeoJson.features.forEach(function (feature) {
                    geometryRings(feature && feature.geometry).forEach(function (ring) {
                        const points = ring.map(function (coordinate) {
                            const projected = L.CRS.EPSG3857.project(
                                L.latLng(Number(coordinate[1]), Number(coordinate[0]))
                            );
                            const x = ((projected.x - xmin) / width) * 1000;
                            const y = ((ymax - projected.y) / height) * 1000;
                            return [x, y];
                        }).filter(function (point) {
                            return point.every(Number.isFinite);
                        });
                        if (points.length < 3) {
                            return;
                        }
                        pathParts.push(
                            "M" + points.map(function (point) {
                                return point[0].toFixed(2) + "," + point[1].toFixed(2);
                            }).join("L") + "Z"
                        );
                    });
                });
                return pathParts.join("");
            };

            const createClippedGeologyOverlay = function (imageUrl, extentInfo) {
                const overlayOptions = {
                    opacity: 0.72,
                    interactive: false,
                    pane: "geologyPane",
                };
                if (!geologyFilterGeoJson) {
                    return L.imageOverlay(imageUrl, extentInfo.bounds, overlayOptions);
                }
                const pathData = geologyClipPath(extentInfo.projectedExtent);
                if (!pathData) {
                    return null;
                }
                const svgNamespace = "http://www.w3.org/2000/svg";
                const svg = document.createElementNS(svgNamespace, "svg");
                svg.setAttribute("viewBox", "0 0 1000 1000");
                svg.setAttribute("preserveAspectRatio", "none");
                const definitions = document.createElementNS(svgNamespace, "defs");
                const clipPath = document.createElementNS(svgNamespace, "clipPath");
                const clipId = "geology-clip-" + requestSequence;
                clipPath.setAttribute("id", clipId);
                const path = document.createElementNS(svgNamespace, "path");
                path.setAttribute("d", pathData);
                path.setAttribute("clip-rule", "evenodd");
                clipPath.appendChild(path);
                definitions.appendChild(clipPath);
                svg.appendChild(definitions);
                const image = document.createElementNS(svgNamespace, "image");
                image.setAttribute("href", imageUrl);
                image.setAttribute("x", "0");
                image.setAttribute("y", "0");
                image.setAttribute("width", "1000");
                image.setAttribute("height", "1000");
                image.setAttribute("preserveAspectRatio", "none");
                image.setAttribute("clip-path", "url(#" + clipId + ")");
                svg.appendChild(image);
                return L.svgOverlay(svg, extentInfo.bounds, overlayOptions);
            };

            const abortExport = function () {
                if (exportController) {
                    exportController.abort();
                    exportController = null;
                }
            };

            const refreshGeologyImage = async function () {
                if (!map.hasLayer(geologyLayer)) {
                    return;
                }
                const bounds = getVisibleGeologyBounds();
                const size = map.getSize();
                if (!bounds || !bounds.isValid()) {
                    if (geologyImageOverlay) {
                        geologyLayer.removeLayer(geologyImageOverlay);
                        geologyImageOverlay = null;
                    }
                    if (geologyImageObjectUrl) {
                        URL.revokeObjectURL(geologyImageObjectUrl);
                        geologyImageObjectUrl = null;
                    }
                    lastExportKey = null;
                    setStatus("Di luar wilayah filter geologi.");
                    return;
                }
                if (!size || size.x <= 0 || size.y <= 0) {
                    return;
                }

                const northWest = map.latLngToContainerPoint(bounds.getNorthWest());
                const southEast = map.latLngToContainerPoint(bounds.getSouthEast());
                const visibleWidth = Math.max(1, Math.abs(southEast.x - northWest.x));
                const visibleHeight = Math.max(1, Math.abs(southEast.y - northWest.y));
                const pixelRatio = Math.max(
                    1, Math.min(2, window.devicePixelRatio || 1)
                );
                const renderScale = map.getZoom() <= 5
                    ? 1.0
                    : (map.getZoom() <= 7 ? 1.5 : 2.0);
                const effectiveScale = Math.min(
                    Math.max(renderScale, pixelRatio),
                    4096 / visibleWidth,
                    4096 / visibleHeight
                );
                const width = Math.max(1, Math.round(visibleWidth * effectiveScale));
                const height = Math.max(1, Math.round(visibleHeight * effectiveScale));
                const imageExtent = extentInfoForBounds(bounds);
                const bboxValues = imageExtent.projectedExtent;
                const currentExportKey = bboxValues.map(function (value) {
                    return Number(value).toFixed(5);
                }).join(",") + "|" + width + "x" + height + "|" + geologyDynamicLayers;
                if (currentExportKey === lastExportKey && geologyImageOverlay) {
                    return;
                }

                abortExport();
                exportController = new AbortController();
                const currentController = exportController;
                const currentSequence = ++requestSequence;
                let pendingObjectUrl = null;
                let exportTimedOut = false;
                const timeoutId = window.setTimeout(
                    function () {
                        exportTimedOut = true;
                        currentController.abort();
                    },
                    30000
                );
                const params = new URLSearchParams({
                    bbox: bboxValues.join(","),
                    bboxSR: "3857",
                    imageSR: "3857",
                    layers: "show:8",
                    size: width + "," + height,
                    format: "png32",
                    transparent: "true",
                    dpi: "96",
                    dynamicLayers: geologyDynamicLayers,
                    f: "image",
                });
                setStatus("Memuat tampilan geologi resmi…");

                try {
                    const response = await fetch(
                        geologyServiceUrl + "/export",
                        {
                            method: "POST",
                            mode: "cors",
                            credentials: "omit",
                            headers: {"Content-Type": "application/x-www-form-urlencoded"},
                            body: params.toString(),
                            signal: currentController.signal,
                        }
                    );
                    if (!response.ok) {
                        throw new Error("HTTP " + response.status);
                    }
                    const contentType = String(
                        response.headers.get("content-type") || ""
                    ).toLowerCase();
                    if (!contentType.startsWith("image/png")) {
                        throw new Error("Respons export bukan PNG");
                    }
                    const imageBlob = await response.blob();
                    if (!imageBlob.size || imageBlob.size > 32 * 1024 * 1024) {
                        throw new Error("Ukuran export geologi tidak valid");
                    }
                    pendingObjectUrl = URL.createObjectURL(imageBlob);
                    if (
                        currentSequence !== requestSequence ||
                        !map.hasLayer(geologyLayer)
                    ) {
                        return;
                    }
                    const nextOverlay = createClippedGeologyOverlay(
                        pendingObjectUrl, imageExtent
                    );
                    if (!nextOverlay) {
                        throw new Error("Geometri filter geologi tidak valid");
                    }
                    nextOverlay.addTo(geologyLayer);
                    if (geologyImageOverlay) {
                        geologyLayer.removeLayer(geologyImageOverlay);
                    }
                    const previousObjectUrl = geologyImageObjectUrl;
                    geologyImageOverlay = nextOverlay;
                    geologyImageObjectUrl = pendingObjectUrl;
                    pendingObjectUrl = null;
                    if (previousObjectUrl) {
                        URL.revokeObjectURL(previousObjectUrl);
                    }
                    lastExportKey = currentExportKey;
                    setStatus("Aktif — klik peta untuk melihat detail formasi.");
                } catch (error) {
                    if (
                        error &&
                        error.name === "AbortError" &&
                        exportTimedOut &&
                        currentSequence === requestSequence &&
                        map.hasLayer(geologyLayer)
                    ) {
                        setStatus("Layanan geologi melewati batas waktu.");
                        console.warn("Timeout saat memuat Layer 8 BIG/KSP.");
                    } else if (!error || error.name !== "AbortError") {
                        setStatus("Layer geologi sementara tidak dapat dimuat.");
                        console.warn("Gagal memuat Layer 8 BIG/KSP.");
                    }
                } finally {
                    window.clearTimeout(timeoutId);
                    if (pendingObjectUrl) {
                        URL.revokeObjectURL(pendingObjectUrl);
                    }
                    if (exportController === currentController) {
                        exportController = null;
                    }
                }
            };

            const scheduleRefresh = function () {
                if (!map.hasLayer(geologyLayer)) {
                    return;
                }
                if (refreshTimer) {
                    window.clearTimeout(refreshTimer);
                }
                refreshTimer = window.setTimeout(refreshGeologyImage, 300);
            };

            const popupRow = function (label, value) {
                if (value == null || String(value).trim() === "") {
                    return "";
                }
                return (
                    "<tr><th style=\"text-align:left;padding-right:8px\">" +
                    escapeGeologyHtml(label) +
                    "</th><td>" + escapeGeologyHtml(value) + "</td></tr>"
                );
            };

            const identifyGeology = async function (event) {
                if (
                    !map.hasLayer(geologyLayer) ||
                    !event ||
                    !event.latlng ||
                    !pointInGeologyFilter(event.latlng)
                ) {
                    return;
                }
                if (identifyController) {
                    identifyController.abort();
                }
                identifyController = new AbortController();
                const currentController = identifyController;
                let identifyTimedOut = false;
                const identifyTimeoutId = window.setTimeout(function () {
                    identifyTimedOut = true;
                    currentController.abort();
                }, 10000);
                const params = new URLSearchParams({
                    where: geologyDefinitionExpression || "1=1",
                    geometry: JSON.stringify({
                        x: event.latlng.lng,
                        y: event.latlng.lat,
                        spatialReference: {wkid: 4326},
                    }),
                    geometryType: "esriGeometryPoint",
                    inSR: "4326",
                    spatialRel: "esriSpatialRelIntersects",
                    outFields: "fcode,simobj,namobj,umurobj,remark",
                    returnGeometry: "false",
                    resultRecordCount: "1",
                    f: "json",
                });

                try {
                    const response = await fetch(
                        geologyServiceUrl + "/8/query",
                        {
                            method: "POST",
                            mode: "cors",
                            credentials: "omit",
                            headers: {"Content-Type": "application/x-www-form-urlencoded"},
                            body: params.toString(),
                            signal: currentController.signal,
                        }
                    );
                    if (!response.ok) {
                        throw new Error("HTTP " + response.status);
                    }
                    const payload = await response.json();
                    if (!map.hasLayer(geologyLayer) || currentController.signal.aborted) {
                        return;
                    }
                    const features = Array.isArray(payload && payload.features)
                        ? payload.features
                        : [];
                    const feature = features[0];
                    if (!feature || !feature.attributes) {
                        return;
                    }
                    const attributes = feature.attributes;
                    const content =
                        "<div class=\"geology-identify-popup\">" +
                        "<strong>Geologi Indonesia</strong>" +
                        "<table style=\"margin-top:5px\">" +
                        popupRow("Formasi", attributes.remark || attributes.namobj) +
                        popupRow("Nama Inggris", attributes.namobj) +
                        popupRow("Umur", attributes.umurobj) +
                        popupRow("Sandi", attributes.simobj) +
                        popupRow("Kode", attributes.fcode) +
                        "</table>" +
                        "<div style=\"margin-top:6px;font-size:10px\">" +
                        "Pusat Survei Geologi, Badan Geologi, KESDM; " +
                        "Kebijakan Satu Peta/BIG.</div></div>";
                    L.popup({maxWidth: 330})
                        .setLatLng(event.latlng)
                        .setContent(content)
                        .openOn(map);
                } catch (error) {
                    if (error && error.name === "AbortError" && identifyTimedOut) {
                        console.warn("Timeout saat mengambil detail Layer 8 BIG/KSP.");
                    } else if (!error || error.name !== "AbortError") {
                        console.warn("Gagal mengambil detail Layer 8 BIG/KSP.");
                    }
                } finally {
                    window.clearTimeout(identifyTimeoutId);
                    if (identifyController === currentController) {
                        identifyController = null;
                    }
                }
            };

            const activateLayer = function () {
                setLegendVisible(true);
                setAttributionVisible(true);
                scheduleRefresh();
            };

            const deactivateLayer = function () {
                requestSequence += 1;
                abortExport();
                if (identifyController) {
                    identifyController.abort();
                    identifyController = null;
                }
                if (refreshTimer) {
                    window.clearTimeout(refreshTimer);
                    refreshTimer = null;
                }
                if (geologyImageOverlay) {
                    geologyLayer.removeLayer(geologyImageOverlay);
                    geologyImageOverlay = null;
                }
                if (geologyImageObjectUrl) {
                    URL.revokeObjectURL(geologyImageObjectUrl);
                    geologyImageObjectUrl = null;
                }
                lastExportKey = null;
                setStatus("Layer nonaktif.");
                setLegendVisible(false);
                setAttributionVisible(false);
            };

            map.on("moveend resize", scheduleRefresh);
            map.on("overlayadd", function (event) {
                if (event.layer === geologyLayer) {
                    activateLayer();
                }
            });
            map.on("overlayremove", function (event) {
                if (event.layer === geologyLayer) {
                    deactivateLayer();
                }
            });
            map.on("click", identifyGeology);
            map.on("unload", deactivateLayer);

            if (geologyLayerInitiallyVisible && map.hasLayer(geologyLayer)) {
                activateLayer();
            } else {
                setLegendVisible(false);
            }
        })();
        {% endmacro %}
        """
    )
    dynamic_layer.add_to(earthquake_map)
    return geology_layer
