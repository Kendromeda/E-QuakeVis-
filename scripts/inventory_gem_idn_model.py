"""Inventory the sources of the GEM IDN v2017.1.0 OpenQuake model.

Needs the OpenQuake environment (Python >= 3.11), not the app environment:
``.venv-oq\\Scripts\\python.exe scripts\\inventory_gem_idn_model.py``.
Parses every source model listed in ``ssmLT.xml`` with OpenQuake's own NRML
reader and writes one row per source to ``data/psha/gem_idn_inventory.csv``
plus totals per source file to ``data/psha/gem_idn_inventory_summary.json``.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
import re
import sys

from openquake.hazardlib import nrml, sourceconverter

ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = ROOT / "data" / "psha" / "gem_idn_v2017.1.0" / "in"
OUTPUT_CSV = ROOT / "data" / "psha" / "gem_idn_inventory.csv"
OUTPUT_SUMMARY = ROOT / "data" / "psha" / "gem_idn_inventory_summary.json"
FIELDS = [
    "file", "source_id", "name", "source_type", "tectonic_region", "num_points",
    "min_mag", "max_mag", "rate_above_min_mag", "upper_seismo_depth_km",
    "lower_seismo_depth_km", "hypo_depths_km", "dip", "rake", "mag_scale_rel",
]


def _source_files() -> list[str]:
    text = (MODEL_DIR / "ssmLT.xml").read_text(encoding="utf-8")
    block = re.search(r"<uncertaintyModel>(.*?)</uncertaintyModel>", text, re.S).group(1)
    return block.split()


def _round(value, digits: int = 4):
    return None if value is None else round(float(value), digits)


def _mfd_range(mfd) -> tuple[float, float, float]:
    pairs = mfd.get_annual_occurrence_rates()
    mags = [mag for mag, _ in pairs]
    return min(mags), max(mags), sum(rate for _, rate in pairs)


def _row(path: str, src) -> dict:
    kind = type(src).__name__
    row = {"file": path, "source_id": src.source_id, "name": src.name,
           "source_type": kind, "tectonic_region": src.tectonic_region_type,
           "mag_scale_rel": type(src.magnitude_scaling_relationship).__name__}
    if kind == "MultiPointSource":
        points = list(src)
        lows = [p.get_min_max_mag()[0] for p in points]
        highs = [p.get_min_max_mag()[1] for p in points]
        rate = sum(sum(r for _, r in p.mfd.get_annual_occurrence_rates()) for p in points)
        depths = sorted({float(d) for p in points for _, d in p.hypocenter_distribution.data})
        row.update(num_points=len(points), min_mag=_round(min(lows)), max_mag=_round(max(highs)),
                   rate_above_min_mag=_round(rate, 6),
                   upper_seismo_depth_km=_round(src.upper_seismogenic_depth),
                   lower_seismo_depth_km=_round(src.lower_seismogenic_depth),
                   hypo_depths_km=" ".join(f"{d:g}" for d in depths))
        planes = {(round(p.dip), round(p.rake)) for _, p in src.nodal_plane_distribution.data}
        row.update(dip=" ".join(sorted({str(d) for d, _ in planes})),
                   rake=" ".join(sorted({str(r) for _, r in planes})))
        return row
    low, high, rate = _mfd_range(src.mfd)
    row.update(num_points=1, min_mag=_round(low), max_mag=_round(high),
               rate_above_min_mag=_round(rate, 6), rake=_round(src.rake, 1))
    if kind == "SimpleFaultSource":
        row.update(upper_seismo_depth_km=_round(src.upper_seismogenic_depth),
                   lower_seismo_depth_km=_round(src.lower_seismogenic_depth),
                   dip=_round(src.dip, 1))
    elif kind == "ComplexFaultSource":
        top, bottom = src.edges[0], src.edges[-1]
        row.update(upper_seismo_depth_km=_round(min(p.depth for p in top)),
                   lower_seismo_depth_km=_round(max(p.depth for p in bottom)))
    return row


def build_inventory() -> tuple[list[dict], dict]:
    # Same discretisation as the model's job.ini.
    converter = sourceconverter.SourceConverter(
        investigation_time=1.0, rupture_mesh_spacing=5.0,
        complex_fault_mesh_spacing=10.0, width_of_mfd_bin=0.1)
    rows, summary = [], {}
    for path in _source_files():
        model = nrml.to_python(str(MODEL_DIR / path), converter)
        file_rows = [_row(path, src) for group in model.src_groups for src in group]
        rows.extend(file_rows)
        summary[path] = {
            "tectonic_regions": sorted({r["tectonic_region"] for r in file_rows}),
            "source_types": sorted({r["source_type"] for r in file_rows}),
            "num_sources": len(file_rows),
            "num_points": sum(r["num_points"] for r in file_rows),
            "max_mag": max(r["max_mag"] for r in file_rows),
            "total_rate_above_min_mag": round(sum(r["rate_above_min_mag"] for r in file_rows), 4),
        }
        print(f"{path}: {len(file_rows)} sources")
    return rows, summary


def main() -> int:
    rows, summary = build_inventory()
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    OUTPUT_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
