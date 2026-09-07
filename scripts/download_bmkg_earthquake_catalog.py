from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import shutil
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


FDSN_ENDPOINT = "https://geof.bmkg.go.id/fdsnws/event/1/query"
INDONESIA_BOUNDS = {
    "minlatitude": -11,
    "maxlatitude": 6,
    "minlongitude": 95,
    "maxlongitude": 141,
}
WIB = timezone(timedelta(hours=7))
SOURCE_NAME = "BMKG FDSN Event Web Service"
OUTPUT_COLUMNS = [
    "event_id",
    "waktu_utc",
    "tanggal",
    "jam",
    "lintang",
    "bujur",
    "kedalaman",
    "magnitudo",
    "tipe_magnitudo",
    "keterangan_lokasi",
    "dirasakan",
    "event_type",
    "sumber",
]


@dataclass(frozen=True)
class Interval:
    start: date
    end: date

    @property
    def name(self) -> str:
        return f"{self.start.isoformat()}_{self.end.isoformat()}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Unduh katalog gempa BMKG untuk wilayah Indonesia dan gabungkan "
            "menjadi satu CSV."
        )
    )
    parser.add_argument("--start", type=date.fromisoformat, default=date(2008, 1, 1))
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=date.today() + timedelta(days=1),
        help="Batas akhir eksklusif (default: besok, agar hari ini tercakup).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/katalog_gempa_bmkg_2008_terbaru.csv"),
    )
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--retries", type=int, default=5)
    return parser.parse_args()


def month_intervals(start: date, end: date) -> list[Interval]:
    if start >= end:
        raise ValueError("Tanggal mulai harus lebih awal daripada tanggal akhir.")
    intervals: list[Interval] = []
    cursor = start
    while cursor < end:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        interval_end = min(next_month, end)
        intervals.append(Interval(cursor, interval_end))
        cursor = interval_end
    return intervals


def query_url(interval: Interval) -> str:
    params = {
        "starttime": interval.start.isoformat(),
        "endtime": interval.end.isoformat(),
        **INDONESIA_BOUNDS,
        "format": "text",
        "orderby": "time-asc",
        "nodata": 404,
    }
    return f"{FDSN_ENDPOINT}?{urlencode(params)}"


def download_interval(
    interval: Interval,
    cache_dir: Path,
    retries: int,
) -> tuple[Interval, Path, int]:
    target = cache_dir / f"{interval.name}.txt"
    if target.exists():
        payload = target.read_bytes()
        return interval, target, max(payload.count(b"\n") - 1, 0)

    request = Request(
        query_url(interval),
        headers={"User-Agent": "Database-Interaktif-BMKG-Catalog/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=180) as response:
                payload = response.read()
            if payload and not payload.startswith(b"#EventID|"):
                raise RuntimeError(
                    f"Format respons tidak dikenali untuk {interval.name}."
                )
            target.write_bytes(payload)
            return interval, target, max(payload.count(b"\n") - 1, 0)
        except HTTPError as exc:
            if exc.code == 404:
                target.write_bytes(b"#EventID|Time|Latitude|Longitude|Depth/km|"
                                   b"Author|Catalog|Contributor|ContributorID|"
                                   b"MagType|Magnitude|MagAuthor|EventLocationName|"
                                   b"EventType\n")
                return interval, target, 0
            last_error = exc
        except (URLError, TimeoutError, OSError, RuntimeError) as exc:
            last_error = exc
        if attempt < retries:
            time.sleep(min(2 ** (attempt - 1), 20))
    raise RuntimeError(
        f"Gagal mengunduh {interval.name} setelah {retries} percobaan: {last_error}"
    )


def parse_utc(value: str) -> datetime:
    normalized = value.strip().removesuffix("Z")
    if "." in normalized:
        main, fraction = normalized.split(".", 1)
        microseconds = int((fraction + "000000")[:6])
    else:
        main = normalized
        microseconds = 0
    parsed = datetime.strptime(main, "%Y-%m-%dT%H:%M:%S")
    return parsed.replace(microsecond=microseconds, tzinfo=timezone.utc)


def canonical_row(source: dict[str, str]) -> dict[str, str]:
    utc_time = parse_utc(source["Time"])
    wib_time = utc_time.astimezone(WIB)
    return {
        "event_id": source["EventID"].strip(),
        "waktu_utc": utc_time.isoformat().replace("+00:00", "Z"),
        "tanggal": f"{wib_time.day}/{wib_time.month}/{wib_time.year}",
        "jam": wib_time.strftime("%H:%M:%S"),
        "lintang": source["Latitude"].strip(),
        "bujur": source["Longitude"].strip(),
        "kedalaman": source["Depth/km"].strip(),
        "magnitudo": source["Magnitude"].strip(),
        "tipe_magnitudo": source["MagType"].strip(),
        "keterangan_lokasi": source["EventLocationName"].strip(),
        "dirasakan": "",
        "event_type": source["EventType"].strip(),
        "sumber": SOURCE_NAME,
    }


def load_rows(paths: list[Path]) -> tuple[list[dict[str, str]], int]:
    by_event_id: dict[str, dict[str, str]] = {}
    raw_count = 0
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter="|")
            if reader.fieldnames:
                reader.fieldnames[0] = reader.fieldnames[0].lstrip("#")
            for source in reader:
                if not source.get("EventID") or not source.get("Time"):
                    continue
                raw_count += 1
                row = canonical_row(source)
                by_event_id[row["event_id"]] = row
    rows = sorted(by_event_id.values(), key=lambda item: item["waktu_utc"])
    return rows, raw_count


def validate_rows(rows: list[dict[str, str]], start: date, end: date) -> None:
    if not rows:
        raise RuntimeError("BMKG tidak mengembalikan satu pun kejadian gempa.")
    for row in rows:
        event_time = parse_utc(row["waktu_utc"])
        if not (start <= event_time.date() < end):
            raise RuntimeError(f"Waktu di luar rentang: {row['event_id']}")
        latitude = float(row["lintang"])
        longitude = float(row["bujur"])
        if not (-11 <= latitude <= 6 and 95 <= longitude <= 141):
            raise RuntimeError(f"Koordinat di luar Indonesia: {row['event_id']}")


def write_csv(output: Path, rows: list[dict[str, str]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)


def main() -> int:
    args = parse_args()
    intervals = month_intervals(args.start, args.end)
    workers = max(1, min(args.workers, 6))
    cache_dir = args.output.parent / ".bmkg_catalog_chunks"
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(
        f"Mengunduh {len(intervals)} interval bulanan dari BMKG "
        f"({args.start} s.d. {args.end - timedelta(days=1)})...",
        flush=True,
    )
    completed: list[tuple[Interval, Path, int]] = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    download_interval, interval, cache_dir, args.retries
                ): interval
                for interval in intervals
            }
            for index, future in enumerate(as_completed(futures), start=1):
                result = future.result()
                completed.append(result)
                if index % 12 == 0 or index == len(intervals):
                    print(
                        f"  {index}/{len(intervals)} interval selesai; "
                        f"{sum(item[2] for item in completed):,} baris sementara",
                        flush=True,
                    )

        completed.sort(key=lambda item: item[0].start)
        rows, raw_count = load_rows([item[1] for item in completed])
        validate_rows(rows, args.start, args.end)
        write_csv(args.output, rows)

        first_time = rows[0]["waktu_utc"]
        last_time = rows[-1]["waktu_utc"]
        duplicates = raw_count - len(rows)
        missing_depth = sum(not row["kedalaman"] for row in rows)
        missing_magnitude = sum(not row["magnitudo"] for row in rows)
        missing_location = sum(not row["keterangan_lokasi"] for row in rows)
        print(f"CSV: {args.output.resolve()}")
        print(f"Baris unik: {len(rows):,}")
        print(f"Duplikat batas interval dihapus: {duplicates:,}")
        print(f"Kedalaman kosong dari sumber: {missing_depth:,}")
        print(f"Magnitudo kosong dari sumber: {missing_magnitude:,}")
        print(f"Nama lokasi kosong dari sumber: {missing_location:,}")
        print(f"Cakupan data tersedia: {first_time} s.d. {last_time}")
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Cache dipertahankan untuk melanjutkan: {cache_dir.resolve()}")
        return 1
    else:
        shutil.rmtree(cache_dir)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
