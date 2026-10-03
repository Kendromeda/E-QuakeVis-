"""Validated, offline Slab2 source grids; never synthesize missing geometry."""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

import numpy as np


class SlabGridError(ValueError):
    """An official cached grid is missing or cannot be safely interpreted."""


def _validate_axis(axis: np.ndarray, name: str, lower: float, upper: float) -> None:
    if axis.ndim != 1 or axis.size < 2 or not np.isfinite(axis).all():
        raise SlabGridError(f"Koordinat {name} Slab2 tidak valid.")
    steps = np.diff(axis)
    if (axis < lower).any() or (axis > upper).any() or (steps <= 0).any():
        raise SlabGridError(f"Koordinat {name} Slab2 harus terurut naik dan dalam batas geografis.")
    if not np.allclose(steps, steps[0], rtol=1e-5, atol=1e-8):
        raise SlabGridError(f"Grid {name} Slab2 harus berinterval tetap.")


@lru_cache(maxsize=12)
def _load_serialized(path: str, modified_ns: int, size: int) -> str:
    """Cache immutable JSON; each public call receives independent lists/dicts."""
    del modified_ns, size  # Included in the cache key to invalidate replaced files.
    try:
        with np.load(path, allow_pickle=False) as archive:
            lon = np.asarray(archive["lon"], dtype=np.float64)
            lat = np.asarray(archive["lat"], dtype=np.float64)
            depth = np.asarray(archive["depth"], dtype=np.float64)
            metadata = json.loads(str(archive["metadata"].item()))
        _validate_axis(lon, "longitude", -180, 180)
        _validate_axis(lat, "latitude", -90, 90)
        if depth.shape != (lat.size, lon.size):
            raise SlabGridError("Dimensi kedalaman Slab2 tidak cocok dengan koordinat.")
        valid = depth[np.isfinite(depth)]
        if np.isinf(depth).any() or valid.size == 0 or (valid < 0).any():
            raise SlabGridError("Kedalaman Slab2 harus kilometer positif; nodata menggunakan NaN.")
        required = ("id", "name", "source", "doi")
        if not isinstance(metadata, dict) or any(
            not isinstance(metadata.get(key), str) or not metadata[key] for key in required
        ):
            raise SlabGridError("Metadata sumber USGS Slab2 tidak lengkap.")
        result = {
            **{key: metadata[key] for key in required},
            "lon": lon.tolist(), "lat": lat.tolist(),
            "depth": np.where(np.isfinite(depth), depth, None).tolist(),
            "metadata": metadata,
        }
        return json.dumps(result, allow_nan=False, separators=(",", ":"))
    except SlabGridError:
        raise
    except (OSError, ValueError, KeyError, TypeError, EOFError, BadZipFile) as exc:
        raise SlabGridError("Grid USGS Slab2 tidak dapat dibaca atau formatnya tidak valid.") from exc


def load_slab_grid(path: str | Path) -> dict[str, Any]:
    """Return original ascending lon/lat nodes and positive-down depth[lat][lon].

    Nodata is ``None`` (JSON null), never extrapolated. No downloads occur here.
    The returned object can be changed without affecting any subsequent caller.
    """
    grid_path = Path(path).resolve()
    try:
        if not grid_path.is_file():
            raise SlabGridError(f"Grid USGS Slab2 tidak ditemukan: {grid_path.name}")
        stat = grid_path.stat()
        return json.loads(_load_serialized(str(grid_path), stat.st_mtime_ns, stat.st_size))
    except OSError as exc:
        raise SlabGridError("Grid USGS Slab2 tidak dapat diakses.") from exc
