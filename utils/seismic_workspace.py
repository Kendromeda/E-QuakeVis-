"""Offline workspace adapter; catalog processing stays separate from rendering.

Browser modules own geodesic math, grid interpolation, plots and interaction state.
All coordinates cross this boundary as geographic degrees / positive-down km.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from utils.hypocenter_3d import (
    build_boundary_paths, build_fault_paths, build_trench_paths,
    build_volcano_points, depth_color, magnitude_radius,
)
from utils.map_builder import get_earthquake_event_id

ASSET_DIR = Path(__file__).parent / "seismic_view"


def prepare_events(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Validate positions, retain stable catalog IDs and explicit missing metadata."""
    required = ("bujur", "lintang", "kedalaman")
    if frame.empty or not all(name in frame for name in required):
        return []
    numeric = frame.assign(**{
        name: pd.to_numeric(frame[name], errors="coerce") for name in required
    })
    valid = (
        np.isfinite(numeric[list(required)]).all(axis=1)
        & numeric.bujur.between(-180, 180)
        & numeric.lintang.between(-90, 90)
        & (numeric.kedalaman >= 0)
    )
    clean = numeric.loc[valid].copy()
    clean["magnitudo"] = pd.to_numeric(clean.get("magnitudo"), errors="coerce")
    events = []
    for row in clean.itertuples(index=False):
        magnitude = float(row.magnitudo) if np.isfinite(row.magnitudo) else None
        time = getattr(row, "waktu", None)
        if time is None or pd.isna(time):
            time = " ".join(str(getattr(row, key, "") or "") for key in ("tanggal", "jam")).strip()
        place = getattr(row, "keterangan_lokasi", None)
        if place is None or pd.isna(place) or not str(place).strip():
            place = getattr(row, "provinsi", "")
        place = "" if place is None or pd.isna(place) else str(place)
        events.append({
            "id": get_earthquake_event_id(row),
            "lon": float(row.bujur), "lat": float(row.lintang),
            "depth": float(row.kedalaman), "magnitude": magnitude,
            "radius": magnitude_radius(magnitude), "color": depth_color(row.kedalaman),
            "time": str(time) if time else "Tidak tersedia", "place": place,
            "label": f"M {magnitude if magnitude is not None else '—'} · {place}",
        })
    return events


def build_workspace_payload(
    frame: pd.DataFrame, *, grids: list[dict],
    fault_geojson=None, volcano_geojson=None, trench_geojson=None,
    boundary_geojson=None, fault_indexes=None, volcano_names=None,
) -> dict[str, Any]:
    events = prepare_events(frame)
    if events:
        lon = np.array([event["lon"] for event in events])
        lat = np.array([event["lat"] for event in events])
        pad = max(0.65, max(np.ptp(lon), np.ptp(lat)) * 0.08)
        bbox = [max(-180, float(lon.min()-pad)), max(-89, float(lat.min()-pad)),
                min(180, float(lon.max()+pad)), min(89, float(lat.max()+pad))]
        center = [float(lon.mean()), float(lat.mean())]
    else:
        bbox, center = [94, -12, 142, 8], [118, -2]
    return {
        "events": events, "dropped": len(frame)-len(events),
        "grids": grids, "center": center, "bbox": bbox,
        "layers": {
            "boundaries": build_boundary_paths(boundary_geojson or {}, step=3),
            "faults": build_fault_paths(fault_geojson or {}, fault_indexes, step=2),
            "trenches": build_trench_paths(trench_geojson or {}),
            "volcanoes": build_volcano_points(volcano_geojson or {}, volcano_names),
        },
    }


@lru_cache(maxsize=1)
def _plotly_bundle() -> str:
    from plotly.offline import get_plotlyjs
    return get_plotlyjs()


def workspace_html(payload: dict[str, Any]) -> str:
    """Embed local assets, escaping HTML-significant JSON to prevent script exits."""
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    for char, escape in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        encoded = encoded.replace(char, escape)
    template = (ASSET_DIR / "workspace.html").read_text(encoding="utf-8")
    scripts = "\n".join((ASSET_DIR / name).read_text(encoding="utf-8") for name in (
        "geometry.js", "plots.js", "view3d.js", "crosssection.js", "map.js", "controller.js",
    ))
    return (template.replace("__STYLE__", (ASSET_DIR / "workspace.css").read_text(encoding="utf-8"))
            .replace("__PLOTLY__", _plotly_bundle())
            .replace("__PAYLOAD__", encoded).replace("__SCRIPTS__", scripts))
