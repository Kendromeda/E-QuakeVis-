"""Download a reviewed USGS ComCat earthquake catalogue for Indonesia.

The downloader queries one calendar year at a time to stay below the ComCat
20,000-event response limit.  Only human-reviewed earthquake events are
accepted, and the final CSV keeps the source quality fields needed for audit.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
QUERY_ENDPOINT = "https://earthquake.usgs.gov/fdsnws/event/1/query"
COUNT_ENDPOINT = "https://earthquake.usgs.gov/fdsnws/event/1/count"
INDONESIA_BOUNDS = {
    "minlatitude": -11,
    "maxlatitude": 6,
    "minlongitude": 95,
    "maxlongitude": 141,
}
WIB = timezone(timedelta(hours=7))
SOURCE_NAME = "USGS ANSS ComCat (reviewed)"
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
    "review_status",
    "network",
    "updated_utc",
    "nst",
    "gap",
    "dmin",
    "rms",
    "horizontal_error_km",
    "depth_error_km",
    "magnitude_error",
    "magnitude_station_count",
    "location_source",
    "magnitude_source",
    "detail_url",
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2008, 11, 1))
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=date.today() + timedelta(days=1),
        help="Batas akhir katalog (inklusif menurut layanan USGS).",
    )
    parser.add_argument("--min-magnitude", type=float, default=4.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "katalog_gempa_usgs_reviewed_m4.csv",
    )
    parser.add_argument("--retries", type=int, default=5)
    return parser.parse_args()


def year_intervals(start: date, end: date) -> list[Interval]:
    if start >= end:
        raise ValueError("Tanggal awal harus lebih kecil dari tanggal akhir.")
    intervals: list[Interval] = []
    cursor = start
    while cursor < end:
        next_year = date(cursor.year + 1, 1, 1)
        interval_end = min(next_year, end)
        intervals.append(Interval(cursor, interval_end))
        cursor = interval_end
    return intervals


def query_parameters(
    start: date,
    end: date,
    min_magnitude: float,
    *,
    output_format: str,
) -> dict[str, object]:
    return {
        "starttime": start.isoformat(),
        "endtime": end.isoformat(),
        **INDONESIA_BOUNDS,
        "minmagnitude": min_magnitude,
        "eventtype": "earthquake",
        "reviewstatus": "reviewed",
        "format": output_format,
    }


def request_bytes(url: str, retries: int) -> bytes:
    request = Request(
        url,
        headers={"User-Agent": "Database-Interaktif-USGS-Catalog/1.0"},
    )
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urlopen(request, timeout=180) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(min(2 ** (attempt - 1), 20))
    raise RuntimeError(f"Permintaan USGS gagal setelah {retries} percobaan: {last_error}")


def expected_count(start: date, end: date, min_magnitude: float, retries: int) -> int:
    params = query_parameters(start, end, min_magnitude, output_format="geojson")
    params.pop("format")
    url = f"{COUNT_ENDPOINT}?{urlencode(params)}"
    payload = request_bytes(url, retries).decode("ascii").strip()
    return int(payload)


def parse_utc(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def optional_number(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    number = float(text)
    if not math.isfinite(number):
        return ""
    return text


def canonical_row(source: dict[str, str], min_magnitude: float) -> dict[str, str]:
    event_id = source.get("id", "").strip()
    event_type = source.get("type", "").strip().lower()
    review_status = source.get("status", "").strip().lower()
    magnitude = float(source.get("mag", ""))
    latitude = float(source.get("latitude", ""))
    longitude = float(source.get("longitude", ""))
    depth = float(source.get("depth", ""))
    if not event_id:
        raise ValueError("USGS mengembalikan kejadian tanpa ID.")
    if event_type != "earthquake" or review_status != "reviewed":
        raise ValueError(
            f"Status kejadian {event_id} tidak lolos: {event_type}/{review_status}."
        )
    if magnitude < min_magnitude or not math.isfinite(magnitude):
        raise ValueError(f"Magnitudo kejadian {event_id} tidak valid: {magnitude}.")
    if not (
        INDONESIA_BOUNDS["minlatitude"] <= latitude <= INDONESIA_BOUNDS["maxlatitude"]
        and INDONESIA_BOUNDS["minlongitude"] <= longitude <= INDONESIA_BOUNDS["maxlongitude"]
    ):
        raise ValueError(f"Koordinat kejadian {event_id} di luar batas Indonesia.")

    utc_time = parse_utc(source.get("time", ""))
    wib_time = utc_time.astimezone(WIB)
    updated = source.get("updated", "").strip()
    updated_utc = parse_utc(updated).isoformat().replace("+00:00", "Z") if updated else ""
    return {
        "event_id": event_id,
        "waktu_utc": utc_time.isoformat().replace("+00:00", "Z"),
        "tanggal": wib_time.date().isoformat(),
        "jam": wib_time.strftime("%H:%M:%S"),
        "lintang": str(latitude),
        "bujur": str(longitude),
        "kedalaman": str(depth),
        "magnitudo": str(magnitude),
        "tipe_magnitudo": source.get("magType", "").strip(),
        "keterangan_lokasi": source.get("place", "").strip(),
        "dirasakan": "",
        "event_type": event_type,
        "review_status": review_status,
        "network": source.get("net", "").strip(),
        "updated_utc": updated_utc,
        "nst": optional_number(source.get("nst")),
        "gap": optional_number(source.get("gap")),
        "dmin": optional_number(source.get("dmin")),
        "rms": optional_number(source.get("rms")),
        "horizontal_error_km": optional_number(source.get("horizontalError")),
        "depth_error_km": optional_number(source.get("depthError")),
        "magnitude_error": optional_number(source.get("magError")),
        "magnitude_station_count": optional_number(source.get("magNst")),
        "location_source": source.get("locationSource", "").strip(),
        "magnitude_source": source.get("magSource", "").strip(),
        "detail_url": source.get("url", "").strip(),
        "sumber": SOURCE_NAME,
    }


def download_interval(
    interval: Interval,
    min_magnitude: float,
    cache_dir: Path,
    retries: int,
) -> list[dict[str, str]]:
    cache_path = cache_dir / f"{interval.name}.csv"
    if not cache_path.exists():
        params = query_parameters(
            interval.start,
            interval.end,
            min_magnitude,
            output_format="csv",
        )
        params.update({"orderby": "time-asc", "limit": 20000})
        payload = request_bytes(f"{QUERY_ENDPOINT}?{urlencode(params)}", retries)
        cache_path.write_bytes(payload)

    text = cache_path.read_text(encoding="utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(text)))
    interval_count = expected_count(
        interval.start,
        interval.end,
        min_magnitude,
        retries,
    )
    if len(rows) != interval_count:
        raise RuntimeError(
            f"Jumlah {interval.name} tidak cocok: unduhan {len(rows):,}, "
            f"USGS {interval_count:,}."
        )
    return [canonical_row(row, min_magnitude) for row in rows]


def write_csv(output: Path, rows: list[dict[str, str]]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(output)


def write_manifest(
    output: Path,
    rows: list[dict[str, str]],
    start: date,
    end: date,
    min_magnitude: float,
) -> None:
    manifest = {
        "source": SOURCE_NAME,
        "source_documentation": "https://earthquake.usgs.gov/data/comcat/index.php",
        "query_endpoint": QUERY_ENDPOINT,
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "filters": {
            "starttime": start.isoformat(),
            "endtime": end.isoformat(),
            **INDONESIA_BOUNDS,
            "minmagnitude": min_magnitude,
            "eventtype": "earthquake",
            "reviewstatus": "reviewed",
        },
        "row_count": len(rows),
        "first_event_utc": rows[0]["waktu_utc"] if rows else None,
        "last_event_utc": rows[-1]["waktu_utc"] if rows else None,
        "unique_event_ids": len({row["event_id"] for row in rows}),
    }
    manifest_path = output.with_suffix(".manifest.json")
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)


def main() -> int:
    args = parse_args()
    intervals = year_intervals(args.start, args.end)
    cache_dir = args.output.parent / ".usgs_reviewed_catalog_chunks"
    cache_dir.mkdir(parents=True, exist_ok=True)

    by_event_id: dict[str, dict[str, str]] = {}
    for position, interval in enumerate(intervals, start=1):
        rows = download_interval(
            interval,
            args.min_magnitude,
            cache_dir,
            max(1, args.retries),
        )
        for row in rows:
            by_event_id[row["event_id"]] = row
        print(
            f"[{position:02d}/{len(intervals):02d}] {interval.name}: "
            f"{len(rows):,} kejadian"
        )

    rows = sorted(by_event_id.values(), key=lambda row: (row["waktu_utc"], row["event_id"]))
    catalogue_count = expected_count(
        args.start,
        args.end,
        args.min_magnitude,
        max(1, args.retries),
    )
    if len(rows) != catalogue_count:
        raise RuntimeError(
            f"Total unik tidak cocok: unduhan {len(rows):,}, USGS {catalogue_count:,}."
        )
    write_csv(args.output, rows)
    write_manifest(args.output, rows, args.start, args.end, args.min_magnitude)
    print(f"Selesai: {len(rows):,} kejadian -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
