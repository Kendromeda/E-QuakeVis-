"""Scene preparation for the 3D hypocentre view.

The view plots every filtered earthquake at its true hypocentre - longitude,
latitude and depth - so the Wadati-Benioff zone becomes visible as a dipping
plane of seismicity. Reference layers (fault traces, volcanoes, Slab2 depth
contours, the trench axis and province outlines) are projected into the same
space.

Depth is the shared visual key: hypocentres and slab contours use one colour
ramp, so a contour and the events clustered around it read as the same depth.

Geometry is projected to kilometres east/north of the scene centre here; the
browser only rotates and draws it. Everything in this module is plain Python
data (dicts, lists, floats), which keeps it testable without a browser.
"""

from __future__ import annotations

import math
from typing import Any, Collection, Iterable, Sequence

import pandas as pd

from utils.volcano_data import get_volcano_feature_name, iter_volcano_points

# Cool (shallow) to warm (deep), matching the legend drawn beside the scene.
DEPTH_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0.0, (56, 189, 248)),
    (30.0, (34, 197, 94)),
    (70.0, (250, 204, 21)),
    (150.0, (251, 146, 60)),
    (300.0, (239, 68, 68)),
    (700.0, (168, 85, 247)),
)
MAX_DEPTH_KM = 700.0
KM_PER_DEGREE_LATITUDE = 110.574
KM_PER_DEGREE_LONGITUDE = 111.320

FAULT_COLOR = "#dc2626"
TRENCH_COLOR = "#0f172a"
VOLCANO_COLOR = "#f97316"
BOUNDARY_COLOR = "#94a3b8"


def depth_color(depth_km: float) -> str:
    """Return a hex colour for a hypocentre or slab contour depth."""
    try:
        depth = float(depth_km)
    except (TypeError, ValueError):
        depth = 0.0
    if depth != depth:  # NaN
        depth = 0.0
    depth = max(0.0, min(MAX_DEPTH_KM, depth))

    for index in range(len(DEPTH_STOPS) - 1):
        low_depth, low_color = DEPTH_STOPS[index]
        high_depth, high_color = DEPTH_STOPS[index + 1]
        if depth <= high_depth:
            span = high_depth - low_depth
            ratio = 0.0 if span <= 0 else (depth - low_depth) / span
            channels = [
                int(round(low + (high - low) * ratio))
                for low, high in zip(low_color, high_color)
            ]
            return "#{:02x}{:02x}{:02x}".format(*channels)
    return "#{:02x}{:02x}{:02x}".format(*DEPTH_STOPS[-1][1])


def magnitude_radius(magnitude: float) -> float:
    """Marker radius in pixels, growing with magnitude."""
    try:
        value = float(magnitude)
    except (TypeError, ValueError):
        value = 0.0
    if value != value:
        value = 0.0
    return round(1.6 + max(value, 0.5) ** 1.7 * 0.42, 2)


def project_km(
    longitude: float,
    latitude: float,
    center_longitude: float,
    center_latitude: float,
) -> tuple[float, float]:
    """Equirectangular projection to kilometres east/north of the centre.

    Over one archipelago this is accurate enough for a depth section and keeps
    the horizontal and vertical axes in the same unit, so the dip angle drawn on
    screen is the real one.
    """
    scale = math.cos(math.radians(center_latitude))
    east = (float(longitude) - center_longitude) * KM_PER_DEGREE_LONGITUDE * scale
    north = (float(latitude) - center_latitude) * KM_PER_DEGREE_LATITUDE
    return east, north


def _iter_line_strings(geometry: dict[str, Any] | None) -> Iterable[list[list[float]]]:
    """Yield coordinate lists from LineString and MultiLineString geometries."""
    if not isinstance(geometry, dict):
        return
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "LineString":
        yield coordinates
    elif geometry_type == "MultiLineString":
        yield from coordinates


def _iter_boundary_rings(geometry: dict[str, Any] | None) -> Iterable[list[list[float]]]:
    """Yield outer rings from Polygon and MultiPolygon geometries."""
    if not isinstance(geometry, dict):
        return
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        if coordinates:
            yield coordinates[0]
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:
            if polygon:
                yield polygon[0]


def _clean_path(
    coordinates: Sequence[Sequence[float]],
    depth_km: float,
    step: int = 1,
) -> list[list[float]]:
    """Return [longitude, latitude, depth_km] triples, dropping bad vertices."""
    path: list[list[float]] = []
    total = len(coordinates)
    for index, point in enumerate(coordinates):
        if step > 1 and index % step and index != total - 1:
            continue
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        try:
            longitude = float(point[0])
            latitude = float(point[1])
        except (TypeError, ValueError):
            continue
        if longitude != longitude or latitude != latitude:
            continue
        path.append([longitude, latitude, float(depth_km)])
    return path


HYPOCENTER_COLUMNS = [
    "lon",
    "lat",
    "depth_km",
    "magnitude",
    "radius",
    "color",
    "label",
]


def build_hypocenters(df: pd.DataFrame) -> pd.DataFrame:
    """One row per plottable earthquake, with depth colour and marker radius.

    Rows without usable coordinates or depth are dropped: a hypocentre cannot be
    placed in space without all three.
    """
    if df.empty:
        return pd.DataFrame(columns=HYPOCENTER_COLUMNS)

    frame = df.copy()
    for column in ("provinsi", "waktu"):
        if column not in frame.columns:
            frame[column] = None

    frame["lon"] = pd.to_numeric(frame.get("bujur"), errors="coerce")
    frame["lat"] = pd.to_numeric(frame.get("lintang"), errors="coerce")
    frame["depth_km"] = pd.to_numeric(frame.get("kedalaman"), errors="coerce")
    frame["magnitude"] = pd.to_numeric(frame.get("magnitudo"), errors="coerce")
    frame = frame.dropna(subset=["lon", "lat", "depth_km"])
    if frame.empty:
        return pd.DataFrame(columns=HYPOCENTER_COLUMNS)

    frame["radius"] = frame["magnitude"].map(magnitude_radius)
    frame["color"] = frame["depth_km"].map(depth_color)

    times = pd.to_datetime(frame["waktu"], errors="coerce").dt.strftime("%d %b %Y %H:%M")
    magnitudes = frame["magnitude"].map(
        lambda value: "M -" if pd.isna(value) else f"M {value:.1f}"
    )
    places = frame["provinsi"].fillna("").astype(str)
    frame["label"] = [
        " · ".join(part for part in (magnitude, f"{depth:.0f} km", place, time) if part)
        for magnitude, depth, place, time in zip(
            magnitudes, frame["depth_km"], places, times.fillna("")
        )
    ]
    return frame[HYPOCENTER_COLUMNS].reset_index(drop=True)


def build_fault_paths(
    fault_geojson: dict[str, Any],
    feature_indexes: Collection[int] | None = None,
    step: int = 1,
) -> list[dict[str, Any]]:
    """Fault traces on the surface plane (depth 0)."""
    selected = None if feature_indexes is None else set(feature_indexes)
    paths: list[dict[str, Any]] = []
    for index, feature in enumerate(fault_geojson.get("features", [])):
        if selected is not None and index not in selected:
            continue
        properties = feature.get("properties") or {}
        name = str(properties.get("nama") or properties.get("Name") or "Sesar")
        for line in _iter_line_strings(feature.get("geometry")):
            path = _clean_path(line, 0.0, step=max(1, int(step)))
            if len(path) >= 2:
                paths.append({"path": path, "name": name, "color": FAULT_COLOR})
    return paths


def build_slab_paths(
    slab_geojson: dict[str, Any],
    step: int = 1,
) -> list[dict[str, Any]]:
    """Slab2 depth contours at their own depth, coloured to match it.

    Sharing the hypocentre colour ramp is the point: where the seismicity colour
    matches the contour it sits on, the events are following the slab surface.
    """
    paths: list[dict[str, Any]] = []
    for feature in slab_geojson.get("features", []):
        properties = feature.get("properties") or {}
        try:
            depth = abs(float(properties.get("depth")))
        except (TypeError, ValueError):
            continue
        for line in _iter_line_strings(feature.get("geometry")):
            path = _clean_path(line, depth, step=max(1, int(step)))
            if len(path) >= 2:
                paths.append(
                    {
                        "path": path,
                        "name": f"Kontur slab {depth:.0f} km",
                        "depth_km": depth,
                        "color": depth_color(depth),
                    }
                )
    return paths


def build_trench_paths(
    trench_geojson: dict[str, Any],
    step: int = 1,
) -> list[dict[str, Any]]:
    """Trench axis lines on the surface plane."""
    paths: list[dict[str, Any]] = []
    for feature in trench_geojson.get("features", []):
        properties = feature.get("properties") or {}
        name = str(properties.get("Name") or properties.get("name") or "Palung")
        for line in _iter_line_strings(feature.get("geometry")):
            path = _clean_path(line, 0.0, step=max(1, int(step)))
            if len(path) >= 2:
                paths.append({"path": path, "name": name, "color": TRENCH_COLOR})
    return paths


def build_boundary_paths(
    region_geojson: dict[str, Any],
    step: int = 3,
) -> list[dict[str, Any]]:
    """Coast and province outlines at depth 0, thinned for rendering speed."""
    paths: list[dict[str, Any]] = []
    for feature in region_geojson.get("features", []):
        for ring in _iter_boundary_rings(feature.get("geometry")):
            path = _clean_path(ring, 0.0, step=max(1, int(step)))
            if len(path) >= 3:
                paths.append({"path": path, "color": BOUNDARY_COLOR})
    return paths


def build_volcano_points(
    volcano_geojson: dict[str, Any],
    volcano_names: Collection[str] | None = None,
) -> list[dict[str, Any]]:
    """Volcano summits as surface markers."""
    points: list[dict[str, Any]] = []
    for feature, latitude, longitude in iter_volcano_points(
        volcano_geojson, volcano_names
    ):
        points.append(
            {
                "lon": float(longitude),
                "lat": float(latitude),
                "name": get_volcano_feature_name(feature),
            }
        )
    return points


def scene_bbox(
    hypocenters: pd.DataFrame,
    padding_ratio: float = 0.12,
    min_padding_deg: float = 0.35,
) -> tuple[float, float, float, float]:
    """Bounding box of the plotted events as (lon_min, lat_min, lon_max, lat_max).

    The margin keeps the surrounding coastline and fault traces visible without
    letting the rest of the archipelago shrink the events to a speck.
    """
    if hypocenters.empty:
        return 94.0, -11.5, 141.5, 6.5
    lon_min = float(hypocenters["lon"].min())
    lon_max = float(hypocenters["lon"].max())
    lat_min = float(hypocenters["lat"].min())
    lat_max = float(hypocenters["lat"].max())
    span = max(lon_max - lon_min, lat_max - lat_min)
    padding = max(span * float(padding_ratio), float(min_padding_deg))
    return (
        lon_min - padding,
        lat_min - padding,
        lon_max + padding,
        lat_max + padding,
    )


def _clip_paths_to_bbox(
    paths: Sequence[dict[str, Any]],
    bbox: tuple[float, float, float, float],
) -> list[dict[str, Any]]:
    """Keep only the runs of a path that fall inside the box.

    A polyline leaving and re-entering the box becomes several shorter
    polylines; runs of a single vertex are dropped because they cannot be drawn.
    """
    lon_min, lat_min, lon_max, lat_max = bbox
    clipped: list[dict[str, Any]] = []
    for entry in paths:
        run: list[list[float]] = []
        for point in entry["path"]:
            inside = lon_min <= point[0] <= lon_max and lat_min <= point[1] <= lat_max
            if inside:
                run.append(point)
                continue
            if len(run) >= 2:
                clipped.append({**entry, "path": run})
            run = []
        if len(run) >= 2:
            clipped.append({**entry, "path": run})
    return clipped


def scene_center(hypocenters: pd.DataFrame) -> tuple[float, float]:
    """Return the (longitude, latitude) the scene is projected around."""
    if hypocenters.empty:
        return 118.0, -2.5
    return float(hypocenters["lon"].mean()), float(hypocenters["lat"].mean())


def _project_paths(
    paths: Sequence[dict[str, Any]],
    center_longitude: float,
    center_latitude: float,
) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for entry in paths:
        points: list[list[float]] = []
        for longitude, latitude, depth in entry["path"]:
            east, north = project_km(
                longitude, latitude, center_longitude, center_latitude
            )
            points.append([round(east, 1), round(north, 1), round(float(depth), 1)])
        if len(points) >= 2:
            projected.append({"p": points, "c": entry.get("color", BOUNDARY_COLOR)})
    return projected


def build_scene(
    filtered_df: pd.DataFrame,
    fault_geojson: dict[str, Any] | None = None,
    volcano_geojson: dict[str, Any] | None = None,
    slab_geojson: dict[str, Any] | None = None,
    trench_geojson: dict[str, Any] | None = None,
    boundary_geojson: dict[str, Any] | None = None,
    fault_indexes: Collection[int] | None = None,
    volcano_names: Collection[str] | None = None,
    stem_min_magnitude: float = 5.0,
) -> dict[str, Any]:
    """Assemble everything the browser needs, already projected to kilometres.

    Depths stay positive in kilometres; the renderer applies the vertical
    exaggeration so the slider can change it without rebuilding the payload.
    """
    hypocenters = build_hypocenters(filtered_df)
    center_longitude, center_latitude = scene_center(hypocenters)
    bbox = scene_bbox(hypocenters)

    east_values: list[float] = []
    north_values: list[float] = []
    for longitude, latitude in zip(hypocenters["lon"], hypocenters["lat"]):
        east, north = project_km(
            longitude, latitude, center_longitude, center_latitude
        )
        east_values.append(round(east, 1))
        north_values.append(round(north, 1))

    # Slab contours run to 700 km everywhere; drawing all of them would stretch
    # the vertical axis far past a shallow filter. The renderer can still reveal
    # the deeper ones on request.
    depth_limit = 150.0
    if not hypocenters.empty:
        deepest = float(pd.to_numeric(hypocenters["depth_km"], errors="coerce").max())
        depth_limit = max(150.0, math.ceil(deepest * 1.3 / 50.0) * 50.0)
    depth_limit = min(depth_limit, MAX_DEPTH_KM)

    magnitudes = pd.to_numeric(hypocenters.get("magnitude"), errors="coerce")
    stem_flags = [
        bool(value >= float(stem_min_magnitude)) if value == value else False
        for value in (magnitudes if len(hypocenters) else [])
    ]

    corners = [
        project_km(bbox[0], bbox[1], center_longitude, center_latitude),
        project_km(bbox[2], bbox[3], center_longitude, center_latitude),
    ]
    scene: dict[str, Any] = {
        "center": [round(center_longitude, 4), round(center_latitude, 4)],
        "bbox": [round(value, 4) for value in bbox],
        "box": {
            "x": [round(corners[0][0], 1), round(corners[1][0], 1)],
            "y": [round(corners[0][1], 1), round(corners[1][1], 1)],
        },
        "depthLimit": depth_limit,
        "maxDepth": MAX_DEPTH_KM,
        "points": {
            "x": east_values,
            "y": north_values,
            "d": [round(float(value), 1) for value in hypocenters["depth_km"]],
            "r": [float(value) for value in hypocenters["radius"]],
            "c": list(hypocenters["color"]),
            "s": stem_flags,
            "label": list(hypocenters["label"]),
        },
        "boundaries": [],
        "faults": [],
        "slabs": [],
        "trenches": [],
        "volcanoes": [],
    }

    # Every reference layer is cut to the same box as the events, and drawn at
    # full resolution because the box is small enough to afford it.
    if boundary_geojson:
        scene["boundaries"] = _project_paths(
            _clip_paths_to_bbox(build_boundary_paths(boundary_geojson, step=2), bbox),
            center_longitude,
            center_latitude,
        )
    if fault_geojson:
        scene["faults"] = _project_paths(
            _clip_paths_to_bbox(
                build_fault_paths(fault_geojson, fault_indexes, step=1), bbox
            ),
            center_longitude,
            center_latitude,
        )
    if slab_geojson:
        scene["slabs"] = _project_paths(
            _clip_paths_to_bbox(build_slab_paths(slab_geojson, step=1), bbox),
            center_longitude,
            center_latitude,
        )
    if trench_geojson:
        scene["trenches"] = _project_paths(
            _clip_paths_to_bbox(build_trench_paths(trench_geojson, step=1), bbox),
            center_longitude,
            center_latitude,
        )
    if volcano_geojson:
        for point in build_volcano_points(volcano_geojson, volcano_names):
            if not bbox[0] <= point["lon"] <= bbox[2]:
                continue
            if not bbox[1] <= point["lat"] <= bbox[3]:
                continue
            east, north = project_km(
                point["lon"], point["lat"], center_longitude, center_latitude
            )
            scene["volcanoes"].append(
                {"x": round(east, 1), "y": round(north, 1), "n": point["name"]}
            )

    return scene


def depth_legend_stops() -> list[tuple[float, str]]:
    """Legend entries as (depth_km, hex colour) for the colour bar."""
    return [(depth, depth_color(depth)) for depth, _ in DEPTH_STOPS]
