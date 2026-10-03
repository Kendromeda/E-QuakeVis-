"""Reproduce offline Slab2 grids from the official USGS published NetCDF files.

Conversion only: ``python -m pip install h5py``. Runtime needs only numpy.
Run ``python scripts/build_slab2_grid.py`` to download the pinned official mirror,
or pass ``--source-dir data/slab2`` to rebuild from included original files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from urllib.request import urlopen

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "aae2e073b361b35fdd19c0bd2d155c1443ed2559"
BASE_URL = (
    "https://raw.githubusercontent.com/DOI-USGS/neic-catalog-separation/"
    f"{COMMIT}/neic_catalog_separation/data/Slab2/"
)
MODELS = {
    "sum": ("Sumatra-Java (Sunda)", "02.23.18", "38f11750125810ac83c3a52a8bf4e08e800bf0fb2ef8ec136a9a5c7987705654"),
    "sul": ("Sulawesi", "02.23.18", "178c4f6506e5ccc5b13700384f32e3413d5a07877e215f1ac6a7942f82532513"),
    "hal": ("Halmahera", "02.23.18", "45f4fd9a33e149712f2256b2f63f7facc448ef2f3e24611896cdc2e7ac37b8e3"),
    "png": ("New Guinea", "02.26.18", "5eb4d617ca4bbe6a8c10d592171bd9ed6f1d915a075d52df13d4051e6c74f1f9"),
    "phi": ("Philippines", "02.26.18", "39ad6a0f3e6f28a9f7a5f34f66ab6025ebcdbd78f211282b56291c7d729aaba4"),
}


def _read_original(source: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    try:
        import h5py
    except ImportError as exc:
        raise RuntimeError("Conversion requires h5py: python -m pip install h5py") from exc
    with h5py.File(source, "r") as grid:
        lon = np.asarray(grid["x"][:], dtype=np.float64)
        lat = np.asarray(grid["y"][:], dtype=np.float64)
        original = np.asarray(grid["z"][:])
        fill = grid["z"].attrs.get("_FillValue", np.nan)
        masked = np.where(original == np.asarray(fill).item(), np.nan, original)
        z = masked * float(grid["z"].attrs.get("scale_factor", 1))
        z = z + float(grid["z"].attrs.get("add_offset", 0))
    if z.shape != (lat.size, lon.size) or not np.isfinite(z).any():
        raise ValueError("Source grid has incompatible dimensions or no valid nodes.")
    if np.isinf(z).any() or np.any(z[np.isfinite(z)] > 0):
        raise ValueError("Published depth grid must use negative elevations below sea level.")
    return lon, lat, z


def _trim_empty_border(lon: np.ndarray, lat: np.ndarray, z: np.ndarray):
    rows, cols = np.where(np.isfinite(z))
    r0, r1 = int(rows.min()), int(rows.max()) + 1
    c0, c1 = int(cols.min()), int(cols.max()) + 1
    x_order, y_order = np.argsort(lon[c0:c1]), np.argsort(lat[r0:r1])
    depth = -z[r0:r1, c0:c1][np.ix_(y_order, x_order)]
    return lon[c0:c1][x_order], lat[r0:r1][y_order], depth, [r0, r1, c0, c1]


def convert_grid(source: Path, destination: Path, model: str) -> dict:
    """Lossless node reordering/sign conversion, trimming only empty border cells."""
    if model not in MODELS:
        raise ValueError(f"Unsupported Slab2 model: {model}")
    lon, lat, z = _read_original(source)
    x, y, depth, crop = _trim_empty_border(lon, lat, z)
    name, version, _ = MODELS[model]
    filename = f"{model}_slab2_dep_{version}.grd"
    metadata = {
        "id": model, "name": name, "source": BASE_URL + filename,
        "doi": "10.5066/F7PV6JNV", "publication_doi": "10.1126/science.aat4723",
        "publisher": "U.S. Geological Survey", "model_version": version,
        "source_repository_commit": COMMIT, "source_file": filename,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "coordinate_reference": "WGS 84 longitude/latitude (EPSG:4326)",
        "depth_convention": "kilometers positive downward from sea level",
        "source_shape": list(z.shape), "shape": list(depth.shape),
        "source_crop_indices": crop, "valid_nodes": int(np.isfinite(depth).sum()),
        "spacing_degrees": [float(x[1] - x[0]), float(y[1] - y[0])],
        "extent": [float(x[0]), float(y[0]), float(x[-1]), float(y[-1])],
        "transformation": (
            "Negate published z to positive-down depth; sort original x/y ascending; "
            "remove only exterior all-NaN rows/columns. All valid source nodes, "
            "source precision, spacing and internal NaN masks are preserved. "
            "No smoothing, triangulation, infill, extrapolation or geometry synthesis."
        ),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(destination, lon=x, lat=y, depth=depth,
                        metadata=np.array(json.dumps(metadata, sort_keys=True)))
    # Validate with the runtime reader before publishing the manifest.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from utils.slab_grid import load_slab_grid
    load_slab_grid(destination)
    manifest = {**metadata, "output_file": destination.name,
                "output_sha256": hashlib.sha256(destination.read_bytes()).hexdigest()}
    destination.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def _download_verified(model: str, directory: Path) -> Path:
    _, version, expected = MODELS[model]
    filename = f"{model}_slab2_dep_{version}.grd"
    with urlopen(BASE_URL + filename, timeout=45) as response:
        raw = response.read(32 * 1024 * 1024 + 1)
    if len(raw) > 32 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError(f"Source integrity verification failed for {filename}.")
    path = directory / filename
    path.write_bytes(raw)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", choices=tuple(MODELS), default=list(MODELS))
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data" / "slab2")
    parser.add_argument("--source-dir", type=Path, help="Use local published .grd files offline")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="usgs-slab2-") as temporary:
        for model in args.models:
            _, version, expected = MODELS[model]
            source = (args.source_dir / f"{model}_slab2_dep_{version}.grd"
                      if args.source_dir else _download_verified(model, Path(temporary)))
            if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                raise ValueError(f"Source integrity verification failed for {source.name}.")
            manifest = convert_grid(source, args.output_dir / f"{model}.npz", model)
            print(f"{model}: {manifest['shape']}, {manifest['valid_nodes']} original nodes")


if __name__ == "__main__":
    main()
