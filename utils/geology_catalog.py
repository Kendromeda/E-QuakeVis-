from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Collection, Iterable
import colorsys
import urllib.parse
import urllib.request


GEOLOGY_MAPSERVER_URL = (
    "https://kspservices.big.go.id/satupeta/rest/services/"
    "PUBLIK/SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer"
)
CGMW_2022_SOURCE_URL = (
    "https://ccgm.org/wp-content/uploads/2016/11/Colour-2022-V7-poster.pdf"
)
ICS_CGMW_TABLE_URL = (
    "https://stratigraphy.org/ICSchart/CGMW_ICS_colour_codes.xlsx"
)
MIXED_FALLBACK_HEX = "#B8B8B8"
GEOLOGY_STYLE_OFFICIAL = "Resmi BIG/KSP"
GEOLOGY_STYLE_CGMW = "CGMW 2022"
GEOLOGY_STYLE_OPTIONS = (GEOLOGY_STYLE_OFFICIAL, GEOLOGY_STYLE_CGMW)


@dataclass(frozen=True)
class CgmwAgeStyle:
    canonical_name: str
    display_name: str
    rgb: tuple[int, int, int]
    cmyk: tuple[int, int, int, int] | None
    hex: str
    status: str = "exact"
    note: str = "RGB digital resmi CGMW 2022."


@dataclass(frozen=True)
class GeologyUnit:
    key: str
    symbol: str
    name_en: str
    age: str
    name_id: str
    feature_count: int = 0

    @property
    def display_name(self) -> str:
        label = self.name_id or self.name_en or "Unit tanpa nama"
        return f"{self.symbol} {label}" if self.symbol else label

    @property
    def description(self) -> str:
        english = f"; nama Inggris {self.name_en}" if self.name_en else ""
        symbol = f"; sandi {self.symbol}" if self.symbol else ""
        return (
            f"Ringkasan atribut resmi Layer 8: {self.name_id or self.name_en or 'unit tanpa nama'}"
            f"{english}; umur sumber {self.age or 'tidak tersedia'}{symbol}."
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _style(
    canonical: str,
    display: str,
    rgb: tuple[int, int, int],
    cmyk: tuple[int, int, int, int],
) -> CgmwAgeStyle:
    return CgmwAgeStyle(
        canonical,
        display,
        rgb,
        cmyk,
        "#" + "".join(f"{channel:02X}" for channel in rgb),
    )


_CGMW_STYLES: dict[str, CgmwAgeStyle] = {
    "Holosen": _style("Holocene", "Holosen", (254, 235, 210), (0, 10, 20, 0)),
    "Kuarter": _style("Quaternary", "Kuarter", (249, 249, 127), (0, 0, 50, 0)),
    "Neogen": _style("Neogene", "Neogen", (255, 230, 25), (0, 10, 90, 0)),
    "Miocene": _style("Miocene", "Miosen", (255, 255, 0), (0, 0, 100, 0)),
    "Miosen": _style("Miocene", "Miosen", (255, 255, 0), (0, 0, 100, 0)),
    "Oligocene": _style("Oligocene", "Oligosen", (253, 192, 122), (0, 25, 45, 0)),
    "Paleogen": _style("Paleogene", "Paleogen", (253, 154, 82), (0, 40, 60, 0)),
    "Kapur": _style("Cretaceous", "Kapur", (127, 198, 78), (50, 0, 75, 0)),
    "Jura": _style("Jurassic", "Jura", (52, 178, 201), (80, 0, 5, 0)),
    "Trias": _style("Triassic", "Trias", (129, 42, 146), (50, 80, 0, 0)),
    "Triassic": _style("Triassic", "Trias", (129, 42, 146), (50, 80, 0, 0)),
    "Mesozoikum": _style("Mesozoic", "Mesozoikum", (103, 197, 202), (60, 0, 10, 0)),
    "Paleozoikum": _style("Paleozoic", "Paleozoikum", (153, 192, 141), (40, 10, 40, 0)),
    "Perm": _style("Permian", "Perm", (240, 64, 40), (5, 75, 75, 0)),
    "Permian": _style("Permian", "Perm", (240, 64, 40), (5, 75, 75, 0)),
    "Carbonifer": _style("Carboniferous", "Karbon", (103, 165, 153), (60, 15, 30, 0)),
    "Karbon": _style("Carboniferous", "Karbon", (103, 165, 153), (60, 15, 30, 0)),
    "Devonian": _style("Devonian", "Devon", (203, 140, 55), (20, 40, 75, 0)),
    "Silurian": _style("Silurian", "Silur", (179, 225, 182), (30, 0, 25, 0)),
    "Ordovician": _style("Ordovician", "Ordovisium", (0, 146, 112), (100, 0, 60, 0)),
    "Prakambrium": _style("Precambrian", "Prakambrium", (247, 67, 112), (0, 75, 30, 0)),
    "Proteroz": _style("Proterozoic", "Proterozoikum", (247, 53, 99), (0, 80, 35, 0)),
}
_MIXED_AGES = {
    "Tersier", "Pra Tersier", "Meso - Paleo", "Paleo - Meso",
    "Permo Karbon", "Pre-Permia",
}

# Nilai ini mengikuti unique-value renderer `umurobj` yang dipublikasikan
# langsung oleh layanan resmi BIG/KSP Layer 8.
_OFFICIAL_BIG_KSP_RGB: dict[str, tuple[int, int, int]] = {
    "Holosen": (139, 80, 199),
    "Kuarter": (98, 194, 50),
    "Neogen": (186, 90, 48),
    "Miocene": (74, 174, 194),
    "Miosen": (74, 174, 194),
    "Oligocene": (194, 45, 97),
    "Paleogen": (201, 179, 79),
    "Pra Tersier": (35, 92, 166),
    "Meso - Paleo": (61, 161, 103),
    "Paleo - Meso": (61, 161, 103),
    "Tersier": (201, 50, 164),
    "Mesozoikum": (37, 35, 166),
    "Jura": (112, 156, 59),
    "Triassic": (181, 38, 52),
    "Trias": (181, 38, 52),
    "Paleozoikum": (50, 191, 170),
    "Perm": (156, 109, 34),
    "Permian": (156, 109, 34),
    "Pre-Permia": (39, 168, 71),
    "Carbonifer": (201, 196, 46),
    "Karbon": (201, 196, 46),
    "Permo Karbon": (57, 130, 179),
    "Kapur": (44, 73, 191),
    "Devonian": (158, 66, 63),
    "Silurian": (153, 36, 173),
    "Ordovician": (156, 51, 121),
    "Prakambrium": (84, 51, 163),
    "Proteroz": (160, 179, 54),
}


def _official_age_style(raw_age: str | None) -> CgmwAgeStyle:
    normalized = str(raw_age or "").strip()
    rgb = _OFFICIAL_BIG_KSP_RGB.get(normalized)
    if rgb is None:
        display = normalized or "Umur tidak tersedia"
        return CgmwAgeStyle(
            display,
            display,
            (184, 184, 184),
            None,
            MIXED_FALLBACK_HEX,
            "unmapped",
            "Fallback netral: kategori tidak terdapat pada renderer resmi BIG/KSP Layer 8.",
        )
    return CgmwAgeStyle(
        normalized,
        normalized,
        rgb,
        None,
        "#" + "".join(f"{channel:02X}" for channel in rgb),
        "official_big_ksp",
        "Warna renderer resmi BIG/KSP Layer 8.",
    )


def get_cgmw_age_style(raw_age: str | None) -> CgmwAgeStyle:
    normalized = str(raw_age or "").strip()
    if normalized in _CGMW_STYLES:
        return _CGMW_STYLES[normalized]
    display = normalized or "Umur tidak tersedia"
    note = (
        "Fallback netral: kategori campuran/legacy ini bukan warna resmi tunggal "
        "CGMW 2022."
    )
    return CgmwAgeStyle(
        display,
        display,
        (184, 184, 184),
        None,
        MIXED_FALLBACK_HEX,
        "mixed_or_legacy" if normalized in _MIXED_AGES else "unmapped",
        note,
    )


def get_geology_age_style(
    raw_age: str | None,
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
) -> CgmwAgeStyle:
    if style_mode == GEOLOGY_STYLE_OFFICIAL:
        return _official_age_style(raw_age)
    if style_mode == GEOLOGY_STYLE_CGMW:
        return get_cgmw_age_style(raw_age)
    raise ValueError(f"Mode warna geologi tidak dikenal: {style_mode}")


def geology_age_legend(
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
) -> tuple[tuple[str, str], ...]:
    grouped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    preferred = (
        ("Holosen", "Holosen"), ("Kuarter", "Kuarter"), ("Neogen", "Neogen"),
        ("Miocene / Miosen", "Miosen"), ("Oligocene", "Oligocene"),
        ("Paleogen", "Paleogen"), ("Kapur", "Kapur"), ("Jurassic (Jura)", "Jura"),
        ("Triassic / Trias", "Trias"), ("Mesozoikum", "Mesozoikum"),
        ("Paleozoikum", "Paleozoikum"), ("Permian (Perm)", "Permian"),
        ("Carbonifer / Karbon", "Karbon"), ("Devonian", "Devonian"),
        ("Silurian", "Silurian"), ("Ordovician", "Ordovician"),
        ("Prakambrium", "Prakambrium"), ("Proteroz", "Proteroz"),
        ("Umur campuran/legacy", "Tersier"),
    )
    for label, raw in preferred:
        row = (label, get_geology_age_style(raw, style_mode).hex)
        if row not in seen:
            grouped.append(row)
            seen.add(row)
    return tuple(grouped)


def _unit_key(symbol: str, name_en: str, age: str, name_id: str) -> str:
    digest = hashlib.sha256(
        "\x1f".join((symbol, name_en, age, name_id)).encode("utf-8")
    ).hexdigest()
    return digest[:20]


def _as_unit(value: GeologyUnit | dict[str, Any]) -> GeologyUnit:
    if isinstance(value, GeologyUnit):
        return value
    return GeologyUnit(
        key=str(value.get("key") or ""),
        symbol=str(value.get("symbol") or value.get("simobj") or "").strip(),
        name_en=str(value.get("name_en") or value.get("namobj") or "").strip(),
        age=str(value.get("age") or value.get("umurobj") or "").strip(),
        name_id=str(value.get("name_id") or value.get("remark") or "").strip(),
        feature_count=int(value.get("feature_count") or 0),
    )


def parse_grouped_query_response(payload: dict[str, Any]) -> tuple[GeologyUnit, ...]:
    if payload.get("error"):
        message = str((payload.get("error") or {}).get("message") or "Query geologi gagal")
        raise ValueError(message)
    records: dict[str, GeologyUnit] = {}
    for feature in payload.get("features") or []:
        attributes = feature.get("attributes") or {}
        symbol = str(attributes.get("simobj") or "").strip()
        name_en = str(attributes.get("namobj") or "").strip()
        age = str(attributes.get("umurobj") or "").strip()
        name_id = str(attributes.get("remark") or "").strip()
        key = _unit_key(symbol, name_en, age, name_id)
        records[key] = GeologyUnit(
            key, symbol, name_en, age, name_id,
            max(0, int(attributes.get("feature_count") or 0)),
        )
    return tuple(sorted(records.values(), key=lambda unit: (unit.age, unit.symbol, unit.name_id, unit.key)))


def merge_geology_units(groups: Iterable[Collection[GeologyUnit]]) -> tuple[GeologyUnit, ...]:
    merged: dict[str, GeologyUnit] = {}
    for group in groups:
        for unit in group:
            previous = merged.get(unit.key)
            if previous is None:
                merged[unit.key] = unit
            else:
                merged[unit.key] = GeologyUnit(
                    unit.key, unit.symbol, unit.name_en, unit.age, unit.name_id,
                    previous.feature_count + unit.feature_count,
                )
    return tuple(sorted(merged.values(), key=lambda unit: (unit.age, unit.symbol, unit.name_id, unit.key)))


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_formation_definition(
    units: Collection[GeologyUnit | dict[str, Any]],
    selected_keys: Collection[str] | None,
) -> str:
    selected = {str(key) for key in (selected_keys or [])}
    if not selected:
        return ""
    allowed = {_as_unit(unit).key: _as_unit(unit) for unit in units}
    unknown_keys = selected.difference(allowed)
    if unknown_keys:
        raise ValueError("Pilihan formasi tidak lagi valid; muat ulang katalog.")
    clauses: list[str] = []
    for key in sorted(selected):
        unit = allowed.get(key)
        if unit is None:
            continue
        parts = [f"simobj = {_sql_literal(unit.symbol)}"]
        if unit.name_id:
            parts.append(f"remark = {_sql_literal(unit.name_id)}")
        if unit.name_en:
            parts.append(f"namobj = {_sql_literal(unit.name_en)}")
        if unit.age:
            parts.append(f"umurobj = {_sql_literal(unit.age)}")
        clauses.append("(" + " AND ".join(parts) + ")")
    expression = " OR ".join(clauses)
    if len(expression) > 12000:
        raise ValueError("Pilihan formasi terlalu banyak untuk satu permintaan.")
    return expression


def _symbol(color: tuple[int, int, int], *, alpha: int = 190) -> dict[str, Any]:
    return {
        "type": "esriSFS",
        "style": "esriSFSSolid",
        "color": [*color, alpha],
        "outline": {"type": "esriSLS", "style": "esriSLSSolid", "color": [70, 70, 70, 150], "width": 0.35},
    }


def _official_symbol(color: tuple[int, int, int]) -> dict[str, Any]:
    return {
        "type": "esriSFS",
        "style": "esriSFSSolid",
        "color": [*color, 255],
        "outline": {
            "type": "esriSLS",
            "style": "esriSLSNull",
            "color": [0, 0, 0, 0],
            "width": 0,
        },
    }


def build_cgmw_renderer() -> dict[str, Any]:
    raw_values = tuple(_CGMW_STYLES) + tuple(sorted(_MIXED_AGES))
    return {
        "type": "uniqueValue",
        "field1": "umurobj",
        "defaultLabel": "Umur belum dipetakan ke CGMW",
        "defaultSymbol": _symbol((210, 210, 210)),
        "uniqueValueInfos": [
            {
                "value": raw,
                "label": raw,
                "description": get_cgmw_age_style(raw).note,
                "symbol": _symbol(get_cgmw_age_style(raw).rgb),
            }
            for raw in raw_values
        ],
    }


def build_official_renderer() -> dict[str, Any]:
    return {
        "type": "uniqueValue",
        "field1": "umurobj",
        "defaultLabel": None,
        "defaultSymbol": None,
        "uniqueValueInfos": [
            {
                "value": raw,
                "label": raw,
                "description": "Renderer resmi BIG/KSP Layer 8.",
                "symbol": _official_symbol(rgb),
            }
            for raw, rgb in _OFFICIAL_BIG_KSP_RGB.items()
        ],
    }


# The map renderer keys on esriGeometry uniqueValue, which accepts at most
# three fields, so a unit's *renderable* identity is symbol + English name +
# age. Two units that differ only by `remark` therefore share a colour - and
# the legend is built from the same key, so it says so honestly rather than
# showing two swatches the map cannot tell apart.
GEOLOGY_UNIT_FIELD_DELIMITER = "|"
_UNIT_COLOR_LIGHTNESS_SPREAD = 0.20
_UNIT_COLOR_SATURATION_SPREAD = 0.14
# Beyond this many units in one age, lightness alone stops separating them and
# a bounded hue rotation joins in - bounded so the family stays recognisable.
_UNIT_COLOR_HUE_SPREAD = 0.030
_UNIT_COLOR_HUE_THRESHOLD = 12
_UNIT_COLOR_MIN_LIGHTNESS = 0.34
_UNIT_COLOR_MAX_LIGHTNESS = 0.86


def geology_render_key(unit: GeologyUnit | dict[str, Any]) -> str:
    """The unit identity the map renderer is able to express."""
    resolved = _as_unit(unit)
    return GEOLOGY_UNIT_FIELD_DELIMITER.join(
        (resolved.symbol, resolved.name_en, resolved.age)
    )


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def build_geology_unit_colors(
    units: Collection[GeologyUnit | dict[str, Any]],
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
) -> dict[str, str]:
    """Give every map unit its own colour, spread inside its age family.

    Follows USGS TM 11-B1: where one age carries many units, the colours "move
    up and (or) down" around the age colour while keeping the spectral order,
    so a reader can still read relative age from colour. The spread is applied
    to lightness and saturation first, and only adds a small bounded hue
    rotation once an age holds more units than tone alone can separate.

    Units are ordered by symbol within an age. That ordering is stable, not a
    true relative-age ordering - Layer 8 exposes one age string per unit and no
    finer stratigraphic position - so the ramp says "these are different units
    of this age", not "this one is older than that one".
    """
    grouped: dict[str, list[str]] = {}
    for unit in units:
        resolved = _as_unit(unit)
        render_key = geology_render_key(resolved)
        siblings = grouped.setdefault(resolved.age, [])
        if render_key not in siblings:
            siblings.append(render_key)

    colors: dict[str, str] = {}
    for age, render_keys in grouped.items():
        base_hex = get_geology_age_style(age, style_mode).hex
        red, green, blue = _hex_to_rgb(base_hex)
        hue, lightness, saturation = colorsys.rgb_to_hls(
            red / 255.0, green / 255.0, blue / 255.0
        )
        ordered = sorted(render_keys)
        total = len(ordered)
        rotate_hue = total > _UNIT_COLOR_HUE_THRESHOLD
        for index, render_key in enumerate(ordered):
            if total == 1:
                colors[render_key] = base_hex
                continue
            # -1 .. +1 across the family: lighter tints first, deeper last.
            offset = -1.0 + 2.0 * index / (total - 1)
            shifted_lightness = min(
                _UNIT_COLOR_MAX_LIGHTNESS,
                max(
                    _UNIT_COLOR_MIN_LIGHTNESS,
                    lightness + offset * _UNIT_COLOR_LIGHTNESS_SPREAD,
                ),
            )
            shifted_saturation = min(
                1.0,
                max(0.10, saturation - offset * _UNIT_COLOR_SATURATION_SPREAD),
            )
            shifted_hue = (
                (hue + offset * _UNIT_COLOR_HUE_SPREAD) % 1.0
                if rotate_hue
                else hue
            )
            channels = colorsys.hls_to_rgb(
                shifted_hue, shifted_lightness, shifted_saturation
            )
            colors[render_key] = _rgb_to_hex(
                tuple(max(0, min(255, round(channel * 255))) for channel in channels)
            )
    return colors


def build_geology_unit_renderer(
    units: Collection[GeologyUnit | dict[str, Any]],
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
    unit_colors: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A uniqueValue renderer that gives each unit its own colour.

    Pass ``unit_colors`` when the legend has already been coloured, so map and
    legend cannot drift apart.
    """
    colors = dict(unit_colors) if unit_colors else build_geology_unit_colors(units, style_mode)
    infos: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in units:
        resolved = _as_unit(unit)
        render_key = geology_render_key(resolved)
        if render_key in seen:
            continue
        seen.add(render_key)
        infos.append({
            "value": render_key,
            "label": resolved.display_name,
            "description": f"Umur {resolved.age or 'tidak tersedia'}.",
            "symbol": _official_symbol(
                _hex_to_rgb(
                    colors.get(render_key)
                    or get_geology_age_style(resolved.age, style_mode).hex
                )
            ),
        })
    return {
        "type": "uniqueValue",
        "field1": "simobj",
        "field2": "namobj",
        "field3": "umurobj",
        "fieldDelimiter": GEOLOGY_UNIT_FIELD_DELIMITER,
        "defaultLabel": "Satuan di luar katalog",
        # Without a default, a polygon whose attributes are missing from the
        # catalog would simply not be drawn - a hole in the map with no hint
        # that anything is absent.
        "defaultSymbol": _official_symbol(_hex_to_rgb(MIXED_FALLBACK_HEX)),
        "uniqueValueInfos": infos,
    }


def build_geology_renderer(
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
    units: Collection[GeologyUnit | dict[str, Any]] = (),
    unit_colors: dict[str, str] | None = None,
) -> dict[str, Any]:
    if style_mode not in GEOLOGY_STYLE_OPTIONS:
        raise ValueError(f"Mode warna geologi tidak dikenal: {style_mode}")
    if units:
        return build_geology_unit_renderer(units, style_mode, unit_colors)
    # No catalog yet: fall back to colouring by age alone.
    if style_mode == GEOLOGY_STYLE_OFFICIAL:
        return build_official_renderer()
    return build_cgmw_renderer()


def build_dynamic_layers(
    units: Collection[GeologyUnit | dict[str, Any]] = (),
    selected_keys: Collection[str] | None = None,
    *,
    show_formation_labels: bool = False,
    formation_label_expression: str = "[remark]",
    style_mode: str = GEOLOGY_STYLE_OFFICIAL,
    unit_colors: dict[str, str] | None = None,
) -> str:
    drawing_info: dict[str, Any] = {
        "renderer": build_geology_renderer(style_mode, units, unit_colors),
        "transparency": 8,
    }
    if show_formation_labels:
        drawing_info["showLabels"] = True
        drawing_info["labelingInfo"] = [{
            "labelExpression": formation_label_expression,
            "labelPlacement": "esriServerPolygonPlacementAlwaysHorizontal",
            "minScale": 5_000_000,
            "maxScale": 0,
            "removeDuplicates": "all",
            "removeDuplicatesDistance": 180,
            "multiPart": "labelLargest",
            "deconflictionStrategy": "static",
            "repeatLabel": False,
            "allowOverrun": False,
            "stackLabel": False,
            "symbol": {
                "type": "esriTS",
                "color": [35, 35, 35, 255],
                "haloColor": [255, 255, 255, 230],
                "haloSize": 1.2,
                "horizontalAlignment": "center",
                "verticalAlignment": "middle",
                "font": {
                    "family": "Times New Roman",
                    "size": 7,
                    "weight": "bold",
                },
            },
        }]
    layer: dict[str, Any] = {
        "id": 8,
        "source": {"type": "mapLayer", "mapLayerId": 8},
        "drawingInfo": drawing_info,
    }
    definition = build_formation_definition(units, selected_keys)
    if definition:
        layer["definitionExpression"] = definition
    return json.dumps([layer], ensure_ascii=False, separators=(",", ":"))


def _rdp_open(points: list[list[float]], epsilon: float) -> list[list[float]]:
    if len(points) <= 2:
        return points
    start, end = points[0], points[-1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = math.hypot(dx, dy)
    maximum, index = -1.0, 0
    for candidate_index, point in enumerate(points[1:-1], 1):
        distance = (
            abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / denominator
            if denominator else math.hypot(point[0] - start[0], point[1] - start[1])
        )
        if distance > maximum:
            maximum, index = distance, candidate_index
    if maximum > epsilon:
        return (
            _rdp_open(points[: index + 1], epsilon)[:-1]
            + _rdp_open(points[index:], epsilon)
        )
    return [start, end]


def _rdp(points: list[list[float]], epsilon: float) -> list[list[float]]:
    if len(points) <= 4:
        return points
    if points[0] != points[-1]:
        return _rdp_open(points, epsilon)
    ring = points[:-1]
    if len(ring) <= 3:
        return points
    anchor = ring[0]
    split_index = max(
        range(1, len(ring)),
        key=lambda index: math.hypot(
            ring[index][0] - anchor[0], ring[index][1] - anchor[1]
        ),
    )
    first_half = _rdp_open(ring[: split_index + 1], epsilon)
    second_half = _rdp_open(ring[split_index:] + [anchor], epsilon)
    simplified = first_half[:-1] + second_half
    if simplified[-1] != simplified[0]:
        simplified.append(simplified[0])
    if len(simplified) >= 4:
        return simplified
    else:
        return points


def _esri_rings(geometry: dict[str, Any], tolerance: float = 0.01) -> list[list[list[float]]]:
    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if geometry.get("type") == "Polygon" else coordinates
    rings: list[list[list[float]]] = []
    for polygon in polygons:
        for ring in polygon:
            cleaned = [[round(float(point[0]), 6), round(float(point[1]), 6)] for point in ring]
            rings.append(_rdp(cleaned, tolerance))
    return rings


def query_geology_units_for_region(
    region_geojson: dict[str, Any],
    region_names: Collection[str],
    *,
    property_name: str,
    timeout: float = 20.0,
    opener: Any = urllib.request.urlopen,
) -> tuple[GeologyUnit, ...]:
    selected_names = {str(name) for name in region_names if str(name)}
    if not selected_names:
        return ()
    rings: list[list[list[float]]] = []
    for feature in region_geojson.get("features", []):
        name = str((feature.get("properties") or {}).get(property_name) or "")
        if name not in selected_names:
            continue
        rings.extend(_esri_rings(feature.get("geometry") or {}))
    if not rings:
        return ()
    params = {
        "where": "1=1",
        "geometry": json.dumps({"rings": rings, "spatialReference": {"wkid": 4326}}, separators=(",", ":")),
        "geometryType": "esriGeometryPolygon",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "simobj,namobj,umurobj,remark",
        "groupByFieldsForStatistics": "simobj,namobj,umurobj,remark",
        "outStatistics": json.dumps([{
            "statisticType": "count", "onStatisticField": "objectid_1",
            "outStatisticFieldName": "feature_count",
        }], separators=(",", ":")),
        "orderByFields": "umurobj,simobj,remark",
        "returnGeometry": "false",
        "f": "json",
    }
    request = urllib.request.Request(
        f"{GEOLOGY_MAPSERVER_URL}/8/query",
        data=urllib.parse.urlencode(params).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": "E-QuakeVis/1.0"},
        method="POST",
    )
    with opener(request, timeout=timeout) as response:
        payload_bytes = response.read(5_000_001)
    if len(payload_bytes) > 5_000_000:
        raise ValueError("Respons katalog geologi terlalu besar.")
    return parse_grouped_query_response(json.loads(payload_bytes.decode("utf-8")))


GEOLOGY_FEATURE_PAGE_SIZE = 1000
GEOLOGY_FEATURE_MAX_BYTES = 25_000_000


def query_geology_features_for_region(
    region_geojson: dict[str, Any],
    region_names: Collection[str],
    property_name: str = "PROVINSI",
    *,
    max_allowable_offset: float = 0.001,
    geometry_precision: int = 5,
    max_features: int = 20_000,
    timeout: float = 90.0,
    opener: Any = urllib.request.urlopen,
) -> tuple[dict[str, Any], ...]:
    """Download Layer 8 polygons intersecting the selected regions.

    Layer 8 caps a response at 1000 records, so this pages with
    ``resultOffset`` until the service stops reporting a transfer limit.
    ``max_allowable_offset`` generalises the geometry - without it a single
    province runs to tens of megabytes and the local renderer crawls.
    """
    selected_names = {str(name) for name in region_names if str(name)}
    if not selected_names:
        return ()
    rings: list[list[list[float]]] = []
    for feature in region_geojson.get("features", []):
        name = str((feature.get("properties") or {}).get(property_name) or "")
        if name not in selected_names:
            continue
        rings.extend(_esri_rings(feature.get("geometry") or {}))
    if not rings:
        return ()

    base = {
        "where": "1=1",
        "geometry": json.dumps(
            {"rings": rings, "spatialReference": {"wkid": 4326}}, separators=(",", ":")
        ),
        "geometryType": "esriGeometryPolygon",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "simobj,namobj,umurobj,remark",
        "returnGeometry": "true",
        "geometryPrecision": str(int(geometry_precision)),
        "maxAllowableOffset": repr(float(max_allowable_offset)),
        "f": "geojson",
        "resultRecordCount": str(GEOLOGY_FEATURE_PAGE_SIZE),
    }
    collected: list[dict[str, Any]] = []
    offset = 0
    while offset < max_features:
        params = {**base, "resultOffset": str(offset)}
        request = urllib.request.Request(
            f"{GEOLOGY_MAPSERVER_URL}/8/query",
            data=urllib.parse.urlencode(params).encode("utf-8"),
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "E-QuakeVis/1.0",
            },
            method="POST",
        )
        with opener(request, timeout=timeout) as response:
            payload_bytes = response.read(GEOLOGY_FEATURE_MAX_BYTES + 1)
        if len(payload_bytes) > GEOLOGY_FEATURE_MAX_BYTES:
            raise ValueError("Respons poligon geologi terlalu besar.")
        payload = json.loads(payload_bytes.decode("utf-8"))
        if payload.get("error"):
            message = str(
                (payload.get("error") or {}).get("message") or "Query geologi gagal"
            )
            raise ValueError(message)
        page = [
            feature
            for feature in (payload.get("features") or [])
            if feature.get("geometry")
        ]
        collected.extend(page)
        # The service reports the limit at the top level of the GeoJSON body.
        if not payload.get("exceededTransferLimit") or not page:
            break
        offset += GEOLOGY_FEATURE_PAGE_SIZE
    return tuple(collected[:max_features])


def query_geology_units_for_province(
    province_geojson: dict[str, Any],
    province_name: str,
    *,
    timeout: float = 20.0,
    opener: Any = urllib.request.urlopen,
) -> tuple[GeologyUnit, ...]:
    return query_geology_units_for_region(
        province_geojson,
        [province_name],
        property_name="PROVINSI",
        timeout=timeout,
        opener=opener,
    )
