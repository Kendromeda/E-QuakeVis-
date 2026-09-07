"""Coastal kabupaten/kota lookup and earthquake distance-to-coast.

The coastline snapshot is built offline by ``scripts/build_coastal_regions.py``.
At runtime the app only reads it: it needs to know which selected kabupaten/kota
face the sea and, for those, how far each earthquake sits from that coast so the
sidebar can offer a distance filter. The distance is the same 2-D epicentral
surface distance the fault filter uses, measured to the nearest seaward boundary
segment -- proximity only, never a statement about what caused an event.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Collection, Iterable

import numpy as np
import pandas as pd

from utils.fault_data import _nearest_segment


class CoastalDataError(ValueError):
    """Raised when the coastal boundary snapshot is missing or malformed."""


def load_coastal_regions(path: str | Path) -> dict[str, dict[str, Any]]:
    """Load the coastal kabupaten/kota snapshot as {name: {provinsi, segmen_pantai}}."""
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CoastalDataError(
            f"Data pesisir tidak dapat dibaca: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise CoastalDataError("Data pesisir bukan objek pemetaan wilayah.")
    return payload


def coastal_region_names(
    coastal_regions: dict[str, dict[str, Any]],
    candidate_names: Collection[str] | None = None,
) -> list[str]:
    """Names that carry a coastline, optionally intersected with a selection."""
    names = set(coastal_regions)
    if candidate_names is not None:
        names &= {str(name) for name in candidate_names}
    return sorted(names)


def _coast_segments(
    coastal_regions: dict[str, dict[str, Any]],
    region_names: Iterable[str],
) -> tuple[np.ndarray, np.ndarray]:
    starts: list[list[float]] = []
    ends: list[list[float]] = []
    for name in region_names:
        entry = coastal_regions.get(str(name))
        if not entry:
            continue
        for start, end in entry.get("segmen_pantai", []):
            starts.append([float(start[0]), float(start[1])])
            ends.append([float(end[0]), float(end[1])])
    if not starts:
        return np.empty((0, 2), dtype=float), np.empty((0, 2), dtype=float)
    return np.asarray(starts, dtype=float), np.asarray(ends, dtype=float)


def distance_to_coast(
    df: pd.DataFrame,
    coastal_regions: dict[str, dict[str, Any]],
    region_names: Collection[str],
) -> np.ndarray:
    """Per-row horizontal distance (km) to the nearest coast of the named regions.

    Returns an all-NaN array when no named region has a coastline, so callers can
    treat "no coastal region selected" and "region has no coast" identically.
    """
    starts, ends = _coast_segments(coastal_regions, region_names)
    if df.empty or starts.size == 0:
        return np.full(len(df), np.nan, dtype=float)

    distances = np.empty(len(df), dtype=float)
    for position, row in enumerate(df.itertuples(index=False)):
        _, distances[position] = _nearest_segment(
            float(row.lintang),
            float(row.bujur),
            starts,
            ends,
        )
    return distances
