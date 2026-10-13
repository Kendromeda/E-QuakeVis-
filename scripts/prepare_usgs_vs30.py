"""Crop the USGS global Vs30 grid to Indonesia for site-specific PSHA.

Download the 30 arc-second grid (netCDF4/HDF5, about 610 MB) once::

    curl -L -o data/vs30/download/global_vs30.grd \\
        https://apps.usgs.gov/shakemap_geodata/vs30/global_vs30.grd
    .venv-oq\\Scripts\\python.exe scripts\\prepare_usgs_vs30.py

The crop covers the ISC download region with margins (lat -15..10,
lon 91..145) and is stored as int16 m/s in ``data/vs30/usgs_vs30_indonesia.npz``
(read by ``utils/vs30.py``); ``data/vs30/manifest.json`` records the source,
its SHA-256 and the citation. Water cells carry USGS's fixed 600 m/s.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.vs30 import CITATION, VS30_PATH  # noqa: E402

SOURCE_URL = "https://apps.usgs.gov/shakemap_geodata/vs30/global_vs30.grd"
DEFAULT_SOURCE = ROOT / "data" / "vs30" / "download" / "global_vs30.grd"
REGION = {"west": 91.0, "east": 145.0, "south": -15.0, "north": 10.0}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 22), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    args = parser.parse_args()
    import h5py

    if not args.source.is_file():
        raise SystemExit(f"{args.source} not found; download it first (see the docstring).")
    with h5py.File(args.source, "r") as grid:
        lon = grid["lon"][:]
        lat = grid["lat"][:]
        cols = np.where((lon >= REGION["west"] - 1e-9) & (lon <= REGION["east"] + 1e-9))[0]
        rows = np.where((lat >= REGION["south"] - 1e-9) & (lat <= REGION["north"] + 1e-9))[0]
        values = grid["z"][rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
    lat_sub = lat[rows[0]:rows[-1] + 1]
    if lat_sub[0] > lat_sub[-1]:  # store rows from south to north
        values, lat_sub = values[::-1], lat_sub[::-1]
    step = float(np.median(np.diff(lon[cols])))
    missing = ~np.isfinite(values)
    vs30 = np.where(missing, 0, np.rint(values)).astype(np.int16)
    VS30_PATH.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(VS30_PATH, vs30=vs30, lon0=float(lon[cols[0]]),
                        lat0=float(lat_sub[0]), step=step)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_url": SOURCE_URL,
        "source_sha256": sha256(args.source),
        "citation": CITATION,
        "method": "USGS global hybrid Vs30: topographic-slope proxy with regional insets",
        "resolution_arcsec": round(step * 3600, 3),
        "region": REGION,
        "shape": list(vs30.shape),
        "cells_missing": int(missing.sum()),
        "vs30_range_m_s": [int(vs30[~missing].min()), int(vs30[~missing].max())],
        "note": "Water cells are 600 m/s in the USGS grid; PSHA sites are on land.",
        "output": str(VS30_PATH.relative_to(ROOT)).replace("\\", "/"),
    }
    (VS30_PATH.parent / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"{vs30.shape} cells, step {step * 3600:.2f}\" -> {VS30_PATH.relative_to(ROOT)} "
          f"({VS30_PATH.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
