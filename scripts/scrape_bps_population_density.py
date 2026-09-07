"""Collect kepadatan penduduk per kecamatan from BPS statistics tables.

BPS renders its statistics tables with Next.js and ships the underlying rows
inside the RSC flight payload. Reading that payload -- rather than the drawn
table -- gives each kecamatan's official `kode_wilayah` and the raw value, so
rows never have to be matched by position.

Some BPS tables mangle densities of 1000+ ("2.875,41" is read as 2,875 and
rounded to "3"), which silently destroys the values for whole regions. Every
year is therefore audited before it is accepted, and the newest surviving year
wins. Rejected years are reported so the damage stays visible.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT_DIR / "output"
# Authoritative {code: {provinsi, nama_kabupaten_kota, domain, jumlah_kecamatan}}
# directory; kecamatan counts come from the BIG boundary snapshots.
REGION_DIRECTORY_PATH = Path(__file__).resolve().parent / "bps_region_directory.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
DECODER = json.JSONDecoder()

# BPS addresses this table as "<opaque id>#<source>[#<region code>]".
TABLE_ID_BASE = "WVRlTTcySlZDa3lUcFp6czNwbHl4QT09#3"
TABLE_URL = "https://{domain}.bps.go.id/id/statistics-table/3/{ident}/x.html?year={year}"
CANDIDATE_YEARS = (2026, 2025, 2024, 2023, 2022, 2021, 2020, 2019, 2018, 2017)

# The sparsest kecamatan in Banten/Jawa is ~110 people/km2, so anything far
# below that on Java is mangled source data rather than a real reading. Sparse
# provinces (e.g. Sumatra's coastal swamp and transmigration districts) go far
# lower legitimately -- Banyuasin II sits at ~8 people/km2 year after year -- so
# the floor is overridable per region via the directory's "min_density" key.
# Cities are never that sparse, so leave their floor at MIN_PLAUSIBLE_DENSITY;
# a city table dipping below it is thousands-truncated, not a real reading.
MIN_PLAUSIBLE_DENSITY = 50.0

# No Indonesian kecamatan is denser than the mid-40,000s (parts of central
# Jakarta), so a value above this cap is a corrupted BPS cell -- e.g. Tanjung
# Jabung Barat serialises "431.745.205.241.691", which the thousands-separator
# stripper turns into 4.3e14. Such a year is rejected outright.
MAX_PLAUSIBLE_DENSITY = 55000.0

# BPS occasionally rounds a whole year's 1000+ densities down to their thousands
# digit ("4.219" -> "4"), which collapses that year's whole distribution. The
# median is the tell: truncation drops it by 10x or more, while real year-to-year
# change barely moves it and a lone inflated cell (or a lone genuinely sparse
# kecamatan) does not move it at all. A year whose median is far below the
# healthiest year's median has been rounded down and is dropped. The comparison
# is intentionally median-based so an inflated bad year cannot poison the check
# the way a per-cell maximum would.
MEDIAN_DROP_RATIO = 4.0


def _province_slug(province: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", province.casefold()).strip("_")


class BpsScrapeError(RuntimeError):
    """Raised when a BPS table cannot be read or fails its audit."""


def _fetch(url: str) -> str:
    # Reading every candidate year for every region is a lot of requests, so be
    # polite and ride out the transient 403/429 throttles BPS returns under load
    # rather than mistaking a throttle for a missing year.
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 429) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise BpsScrapeError(f"gagal mengambil {url}")


def _rsc_payload(html: str) -> str:
    """Reassemble the Next.js flight payload from its script chunks."""
    parts: list[str] = []
    for match in re.finditer(r'self\.__next_f\.push\(\[1,\s*"', html):
        try:
            text, _ = DECODER.raw_decode(html[match.end() - 1 :])
        except ValueError:
            continue
        parts.append(text)
    return "".join(parts)


def _value_after(flight: str, key: str, start: int = 0):
    index = flight.find(key, start)
    if index < 0:
        raise BpsScrapeError(f"{key} missing from BPS payload")
    value, _ = DECODER.raw_decode(flight, index + len(key))
    return value, index


def _table_identifier(region_code: str | None) -> str:
    raw = TABLE_ID_BASE if region_code is None else f"{TABLE_ID_BASE}#{region_code}"
    return urllib.parse.quote(base64.b64encode(raw.encode()).decode(), safe="")


def _parse_density(raw: object, scale: float = 1.0) -> float | None:
    if raw in (None, "", "None"):
        return None
    try:
        return float(str(raw).replace(".", "").replace(",", ".")) * scale
    except ValueError:
        return None


def _density_column_id(columns: dict) -> str:
    """Return the density variable id without relying on column order."""
    density_ids = [
        variable_id
        for variable_id, meta in columns.items()
        if "kepadatan" in str(meta.get("nama_variabel", "")).casefold()
        or "population density" in str(
            meta.get("nama_variabel_en", "")
        ).casefold()
    ]
    if len(density_ids) != 1:
        raise BpsScrapeError(f"found {len(density_ids)} density columns")
    return density_ids[0]


def read_table(
    domain: str,
    year: int,
    region_code: str | None = None,
    density_scale: float = 1.0,
) -> dict:
    """Return the density rows BPS publishes for one region and year."""
    if density_scale <= 0:
        raise ValueError("density_scale harus lebih besar dari nol")
    url = TABLE_URL.format(
        domain=domain, ident=_table_identifier(region_code), year=year
    )
    flight = _rsc_payload(_fetch(url))
    columns, columns_at = _value_after(flight, '"kolom":')
    data, _ = _value_after(flight, '"data":', columns_at)
    table_year, _ = _value_after(flight, '"tahun_data":')
    title, _ = _value_after(flight, '"judul_tabel":')

    try:
        density_id = _density_column_id(columns)
    except BpsScrapeError as exc:
        raise BpsScrapeError(f"{domain}: {exc}") from exc

    rows = []
    for entry in data:
        code = entry.get("kode_wilayah")
        # Codes ending in 000 are the region total, not a kecamatan.
        if not isinstance(code, int) or code % 1000 == 0:
            continue
        cell = (entry.get("variables") or {}).get(density_id) or {}
        rows.append(
            {
                "kode_wilayah": code,
                "kecamatan": entry.get("label_raw") or entry.get("label"),
                "kepadatan": _parse_density(
                    cell.get("value_raw"), scale=density_scale
                ),
            }
        )
    return {
        "tahun": table_year,
        "judul": title,
        "url": url,
        "rows": rows,
        "density_scale": density_scale,
    }


def audit(
    rows: list[dict], expected: int, min_density: float = MIN_PLAUSIBLE_DENSITY
) -> str | None:
    """Describe why a year is unusable, or None when it looks sound."""
    if len(rows) != expected:
        return f"jumlah kecamatan {len(rows)} (harusnya {expected})"
    values = [row["kepadatan"] for row in rows]
    missing = sum(1 for value in values if value is None)
    if missing:
        return f"{missing} nilai kosong"
    corrupt = sum(1 for value in values if value > MAX_PLAUSIBLE_DENSITY)
    if corrupt:
        return f"{corrupt} nilai tidak wajar (> {MAX_PLAUSIBLE_DENSITY:g} jiwa/km2)"
    mangled = sum(1 for value in values if value < min_density)
    if mangled:
        return f"{mangled} nilai rusak (< {min_density:g} jiwa/km2)"
    if len(set(values)) == 1:
        return f"semua nilai identik ({values[0]:g})"
    return None


def _median_density(rows: list[dict]) -> float:
    """Median density of a table, ignoring blanks and corrupted-high cells."""
    values = sorted(
        row["kepadatan"]
        for row in rows
        if row["kepadatan"] and row["kepadatan"] <= MAX_PLAUSIBLE_DENSITY
    )
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2


def _healthiest_median(tables: list[dict]) -> float:
    """The largest table median across the structurally sound fetched years."""
    return max((_median_density(table["rows"]) for table in tables), default=0.0)


def _truncation_problem(rows: list[dict], reference_median: float) -> str | None:
    """Flag a year whose median sits far below the healthiest year's median."""
    median = _median_density(rows)
    if reference_median and median * MEDIAN_DROP_RATIO < reference_median:
        return (
            f"nilai terpotong (median {median:g} vs {reference_median:g} "
            "di tahun lain)"
        )
    return None


def collect(regions: dict[str, dict]) -> tuple[dict[str, dict], list]:
    collected: dict[str, dict] = {}
    gaps: list[tuple[str, str, list[str]]] = []
    for code, entry in regions.items():
        name = entry["nama_kabupaten_kota"]
        domain = entry["domain"]
        expected = entry["jumlah_kecamatan"]
        # Cities are never genuinely sparse, so a sub-50 value there is
        # truncated rather than real; regencies may dip far lower.
        min_density = entry.get("min_density", MIN_PLAUSIBLE_DENSITY)
        rejected: list[str] = []
        # Fetch every year up front: the structurally sound ones anchor the
        # truncation check, and the audit-clean ones are the candidates.
        fetched: list[dict] = []
        for year in CANDIDATE_YEARS:
            scale = float(
                (entry.get("density_scale_by_year") or {}).get(str(year), 1.0)
            )
            try:
                # Supplying the BPS region code avoids ambiguous/base table
                # routes on sites whose statistics tables use a different
                # column layout or require a region-specific identifier.
                try:
                    table = read_table(
                        domain,
                        year,
                        region_code=code,
                        density_scale=scale,
                    )
                except (BpsScrapeError, OSError):
                    # A few BPS sites expose only the base identifier. The
                    # variable-name lookup remains order-independent on both
                    # routes, so falling back does not reintroduce positional
                    # column assumptions.
                    table = read_table(
                        domain,
                        year,
                        density_scale=scale,
                    )
            except (BpsScrapeError, OSError):
                rejected.append(f"{year}: tidak tersedia")
                continue
            fetched.append(table)

        structural = [
            table
            for table in fetched
            if len(table["rows"]) == expected
            and all(row["kepadatan"] is not None for row in table["rows"])
        ]
        reference_median = _healthiest_median(structural)

        for table in fetched:  # newest-first
            year = table["tahun"]
            problem = audit(table["rows"], expected, min_density)
            if problem is None:
                problem = _truncation_problem(table["rows"], reference_median)
            if problem:
                rejected.append(f"{year}: {problem}")
                continue
            table.update(
                {
                    "code": code,
                    "provinsi": entry["provinsi"],
                    "nama_kabupaten_kota": name,
                    "domain": domain,
                    "rejected": rejected,
                }
            )
            if table.get("density_scale", 1.0) != 1.0:
                table["catatan_sumber"] = (
                    "Tabel statistik BPS; skala desimal pada payload BPS "
                    f"dinormalisasi x{table['density_scale']:g}."
                )
            collected[code] = table
            print(
                f"{code} {name:26s} tahun={year} "
                f"n={len(table['rows']):2d} ditolak={rejected or '-'}"
            )
            break
        else:
            # Some regencies publish no undamaged table for any year. Record the
            # gap instead of aborting so the rest of the province still builds.
            gaps.append((code, name, rejected))
            print(f"{code} {name:26s} TIDAK ADA TAHUN BERSIH")
    return collected, gaps


def main(provinces: list[str] | None = None) -> None:
    directory = json.loads(REGION_DIRECTORY_PATH.read_text(encoding="utf-8"))
    by_province: dict[str, dict] = {}
    for code, entry in directory.items():
        by_province.setdefault(entry["provinsi"], {})[code] = entry

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for province, regions in by_province.items():
        if provinces and province not in provinces:
            continue
        collected, gaps = collect(regions)
        path = OUTPUT_DIR / f"bps_{_province_slug(province)}_scrape.json"
        path.write_text(
            json.dumps(collected, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        total = sum(len(entry["rows"]) for entry in collected.values())
        print(
            f"\n{province}: {len(collected)}/{len(regions)} kabupaten/kota, "
            f"{total} kecamatan -> {path.name}"
        )
        if gaps:
            print(f"  tanpa tabel bersih: {[g[1] for g in gaps]}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:] or None)
