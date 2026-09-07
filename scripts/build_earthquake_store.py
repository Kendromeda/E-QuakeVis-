from __future__ import annotations

import argparse
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.earthquake_store import (
    ensure_working_database,
    get_store_summary,
)
from utils.fault_data import load_fault_geojson
from utils.region_data import load_region_geojson


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bangun indeks kerja SQLite dari arsip CSV gempa."
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=ROOT / "data" / "gempa_working.sqlite",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    catalog_path = ROOT / "data" / "katalog_gempa_bmkg_2008_terbaru.csv"
    master_path = ROOT / "data" / "gempa_master.csv"
    archive_paths = (
        [master_path]
        if master_path.exists()
        else [catalog_path]
        if catalog_path.exists()
        else []
    )
    fault_path = ROOT / "data" / "sesar_pusgen_2024.geojson"
    province_path = ROOT / "data" / "provinsi_indonesia_38.geojson"
    district_path = ROOT / "data" / "kabupaten_kota_indonesia.geojson"
    reference_paths = [fault_path, province_path, district_path]

    rebuilt = ensure_working_database(
        args.database,
        archive_paths,
        reference_paths,
        load_fault_geojson(fault_path),
        load_region_geojson(province_path),
        load_region_geojson(district_path),
        progress=lambda message: print(message, flush=True),
    )
    summary = get_store_summary(args.database)
    action = "Dibangun" if rebuilt else "Sudah mutakhir"
    print(f"{action}: {args.database.resolve()}")
    print(f"Baris: {int(summary['row_count']):,}")
    print(
        f"Cakupan: {summary['minimum_time']} s.d. {summary['maximum_time']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
