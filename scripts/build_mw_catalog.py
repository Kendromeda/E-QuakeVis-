"""Build the merged Mw catalogue (one Mw per event) for Tahap B source models.

Run once after ``scripts/download_isc_bulletin.py`` and
``scripts/match_bmkg_isc.py`` (which fits the magnitude conversions)::

    python scripts/build_mw_catalog.py

The raw ISC Bulletin (280k events, 1.2M magnitudes) is read here only; source
models for a chosen area read the compact result. ISC-GEM
(``data/psha/iscgem/``) is joined when present. How each Mw is chosen is
described in ``utils/mw_catalog.py``.

Outputs in ``data/psha/catalog/``:

- ``mw_catalog.csv``: one row per event with Mw >= ``--min-mw``
- ``mw_catalog_processing.csv``: what was removed at each step and why
- ``mw_catalog_excluded_review.csv``: left-out events that some agency gave a
  magnitude >= 4.5, with the reason (for review)
- ``mw_catalog_duplicates.csv``: second reports of one earthquake that were removed
- ``mw_catalog_sources.csv``: where the Mw of the events came from
- ``mw_catalog_conversions.csv``: conversion equations and how often each was used
- ``mw_catalog_by_decade.csv``: events per decade and Mw origin
- ``manifest.json``: inputs (with SHA-256), settings and counts
- ``analisis_katalog_mw.xlsx``: all of the above as sheets of one workbook
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from match_bmkg_isc import ISC_DIR, ISCGEM_DIR, iscgem_magnitudes  # noqa: E402
from utils.analysis_workbook import write_workbook  # noqa: E402
from utils.iscgem import load_iscgem  # noqa: E402
from utils.mw_catalog import (  # noqa: E402
    build_catalog,
    conversions_table,
    iscgem_only_events,
    load_conversions,
    summarize_by_period,
    summarize_sources,
)

OUT_DIR = ROOT / "data" / "psha" / "catalog"
WORKBOOK = "analisis_katalog_mw.xlsx"
DEFAULT_MIN_MW = 4.0
STEP_DESCRIPTIONS = {
    "events_in": "Event dalam buletin ISC yang diunduh (ditambah event khusus ISC-GEM)",
    "removed_bad_time": "Dibuang: waktu tidak terbaca",
    "removed_not_earthquake": "Dibuang: bukan gempa tektonik (mis. ledakan tambang)",
    "removed_outside_region": "Dibuang: episenter utama di luar kotak unduhan",
    "events_kept": "Event gempa di dalam kotak",
    "depth_fixed": "Di antaranya kedalaman tetap/kosong (0, 10, 33 km), hanya ditandai",
    "without_mw": "Tanpa Mw (tidak ada Mw langsung, proxy ISC-GEM, atau konversi yang sah)",
    "below_min_mw": "Mw di bawah batas minimum katalog",
    "excluded_with_large_magnitude": "Tidak masuk katalog padahal ada magnitudo >= 4,5 "
                                     "(lihat mw_catalog_excluded_review.csv)",
    "removed_duplicates": "Dibuang: laporan ganda satu gempa (<= 16 detik, <= 100 km; "
                          "lihat mw_catalog_duplicates.csv)",
    "probably_unreviewed": "Di antaranya event 24 bulan terakhir, kemungkinan belum direview ISC",
    "events_in_catalog": "Event dalam katalog Mw gabungan",
    "min_mw": "Batas minimum Mw katalog",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-mw", type=float, default=DEFAULT_MIN_MW,
                        help="smallest Mw kept in the catalogue")
    args = parser.parse_args()

    isc_manifest = json.loads((ISC_DIR / "manifest.json").read_text(encoding="utf-8"))
    summary_path = ISC_DIR / "magnitude_conversion_summary.json"
    rules = load_conversions(json.loads(summary_path.read_text(encoding="utf-8")))
    events_path, mags_path = ISC_DIR / "isc_events.csv", ISC_DIR / "isc_magnitudes.csv"
    region = isc_manifest["region"]
    events = pd.read_csv(events_path, dtype={"isc_evid": str})
    gem_files = [(ISCGEM_DIR / name, supplement) for name, supplement in
                 (("isc-gem-cat.csv", False), ("isc-gem-suppl.csv", True))
                 if (ISCGEM_DIR / name).is_file()]
    if gem_files:
        gem_events = pd.concat([load_iscgem(path, supplement=s) for path, s in gem_files],
                               ignore_index=True)
        extra = iscgem_only_events(gem_events, events, region)
        events = pd.concat([events, extra], ignore_index=True)
        print(f"{len(extra)} ISC-GEM events in the region are missing from the ISC "
              "download; added")
    mags = pd.read_csv(mags_path, dtype={"isc_evid": str},
                       usecols=["isc_evid", "agency", "mag_type", "magnitude", "uncertainty"])
    gem = iscgem_magnitudes()
    mags = pd.concat([mags, gem.loc[gem["isc_evid"].isin(events["isc_evid"])]],
                     ignore_index=True)
    print(f"{len(events)} events, {len(mags)} magnitudes (incl. ISC-GEM)")

    retrieved = pd.Timestamp(isc_manifest["retrieved_utc"])
    catalog, stats, excluded, dupes = build_catalog(events, mags, rules, region, args.min_mw,
                                                    retrieved)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    catalog.to_csv(OUT_DIR / "mw_catalog.csv", index=False)
    excluded.to_csv(OUT_DIR / "mw_catalog_excluded_review.csv", index=False)
    dupes.to_csv(OUT_DIR / "mw_catalog_duplicates.csv", index=False)
    pd.DataFrame([{"step": key, "count": value, "description": STEP_DESCRIPTIONS[key]}
                  for key, value in stats.items()]).to_csv(
        OUT_DIR / "mw_catalog_processing.csv", index=False)
    summarize_sources(catalog).to_csv(OUT_DIR / "mw_catalog_sources.csv", index=False)
    conversions_table(rules, catalog).to_csv(OUT_DIR / "mw_catalog_conversions.csv", index=False)
    summarize_by_period(catalog).to_csv(OUT_DIR / "mw_catalog_by_decade.csv", index=False)

    inputs = [events_path, mags_path, summary_path,
              *sorted((ROOT / "data" / "psha" / "iscgem").glob("isc-gem-*.csv"))]
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "description": "Katalog Mw gabungan (satu Mw per event) dari buletin ISC dan ISC-GEM",
        "region": isc_manifest["region"],
        "isc_retrieved_utc": isc_manifest.get("retrieved_utc"),
        "min_mw": args.min_mw,
        "mw_priority": ["direct (GCMT > ISC-GEM > NEIC Mww > NEIC Mw > GFZ > lainnya)",
                        "iscgem_proxy",
                        "converted (rerata berbobot 1/sigma^2 dari konversi di rentang sah)"],
        "inputs": {str(p.relative_to(ROOT)).replace("\\", "/"): sha256(p) for p in inputs},
        "counts": stats,
        "citation": [isc_manifest.get("citation"),
                     "ISC-GEM Earthquake Catalogue, https://doi.org/10.31905/D808B825"],
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)
                                           + "\n", encoding="utf-8")
    write_workbook(OUT_DIR / WORKBOOK, {
        "01 Pemrosesan": pd.read_csv(OUT_DIR / "mw_catalog_processing.csv"),
        "02 Asal Mw": pd.read_csv(OUT_DIR / "mw_catalog_sources.csv"),
        "03 Konversi magnitudo": pd.read_csv(OUT_DIR / "mw_catalog_conversions.csv"),
        "04 Per dekade": pd.read_csv(OUT_DIR / "mw_catalog_by_decade.csv"),
        "05 Tidak masuk (review)": excluded,
        "06 Duplikat dibuang": dupes,
        "07 Katalog Mw": catalog,
    })
    for key, value in stats.items():
        print(f"  {STEP_DESCRIPTIONS[key]}: {value}")
    print(f"Written to {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
