from __future__ import annotations

import csv
import json
import math
import re
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.population_density import (  # noqa: E402
    normalize_region_name,
    normalize_subdistrict_name,
)


OUTPUT_DIR = ROOT / "output"
DATA_DIR = ROOT / "data"
REGION_DIR = DATA_DIR / "bps_kepadatan_per_kabupaten_kota"
SUBDISTRICT_PATH = DATA_DIR / "kecamatan_lightweight.geojson"

PRIMARY_PATH = OUTPUT_DIR / "bps_jabar_scrape.json"
RETRY_PATH = OUTPUT_DIR / "bps_jabar_retry.json"
MISSING_PATH = OUTPUT_DIR / "bps_jabar_missing.json"

# Audited province scrapes that share one schema (see _scrape_rows).
SCRAPE_PATHS = (
    OUTPUT_DIR / "bps_banten_scrape.json",
    OUTPUT_DIR / "bps_jawa_tengah_scrape.json",
    OUTPUT_DIR / "bps_jawa_timur_scrape.json",
    OUTPUT_DIR / "bps_dki_jakarta_scrape.json",
    OUTPUT_DIR / "bps_daerah_istimewa_yogyakarta_scrape.json",
    OUTPUT_DIR / "bps_sumatera_barat_scrape.json",
    OUTPUT_DIR / "bps_jambi_scrape.json",
    OUTPUT_DIR / "bps_sumatera_selatan_scrape.json",
    OUTPUT_DIR / "bps_bengkulu_scrape.json",
    OUTPUT_DIR / "bps_lampung_scrape.json",
)

COMBINED_PATH = DATA_DIR / "kepadatan_penduduk_kecamatan.csv"
MANIFEST_PATH = DATA_DIR / "kepadatan_penduduk_sumber_bps.csv"

JAWA_BARAT_CODES = {
    "3201", "3202", "3203", "3204", "3205", "3206", "3207", "3208",
    "3209", "3210", "3211", "3212", "3213", "3214", "3215", "3216",
    "3217", "3218", "3271", "3272", "3273", "3274", "3275", "3276",
    "3277", "3278", "3279",
}
# 627 Jawa Barat + 155 Banten + 576 Jawa Tengah + 636 Jawa Timur (36 of 38;
# Lamongan and Kota Mojokerto have no undamaged BPS table) + 42 DKI Jakarta
# (5 of 6 cities/regency; Kepulauan Seribu is not published per-kecamatan)
# + 78 Daerah Istimewa Yogyakarta (5 of 5, complete) = 2114 for Java.
# Plus 871 across five Sumatra provinces: 179 Sumatera Barat (19 of 19),
# 133 Jambi (10 of 11; Sarolangun has no undamaged BPS table), 241 Sumatera
# Selatan (17 of 17), 117 Bengkulu (9 of 10; Lebong has no undamaged table),
# and 201 Lampung (14 of 15; Lampung Tengah has no undamaged table -- its only
# populated years are thousands-truncated or corrupted high).
EXPECTED_SUBDISTRICTS = 2985

# A corrupted density must never survive merely because it stays below the
# global density cap. Multiplying the density by the matching BIG polygon area
# catches BPS payloads whose decimal separator disappeared (100x inflation).
MAX_IMPLIED_SUBDISTRICT_POPULATION = 1_000_000.0
EARTH_RADIUS_KM = 6371.0088

# BIG includes Nusakambangan in the Cilacap Selatan polygon (~113.6 km2), while
# BPS explicitly calculates its official density from the inhabited 8.04 km2
# urban area. Use the BPS denominator for this one documented source mismatch;
# otherwise the official 10,607 jiwa/km2 value would be a false positive.
VALIDATION_AREA_OVERRIDES_KM2 = {
    ("cilacap", "cilacapselatan"): 8.04,
}

# Region codes whose values come from a BPS publication rather than a live
# statistics table; filled while reading the scrapes (see _scrape_rows).
FALLBACK_CODES: set[str] = set()

OVERRIDE_CODES = {"3206", "3208", "3211", "3213", "3218", "3271", "3276"}

COMBINED_FIELDS = [
    "kode_bps_kabupaten_kota",
    "provinsi",
    "nama_kabupaten_kota",
    "nama_kecamatan",
    "tahun",
    "kepadatan_penduduk_per_km2",
    "sumber_bps_url",
    "sumber_bps_judul",
    "sumber_bps_diperbarui",
    "catatan_sumber",
]

CIREBON_PUBLICATION_URL = (
    "https://cirebonkota.bps.go.id/id/publication/2025/02/28/"
    "174d698afda79eb54e0f4320/kota-cirebon-dalam-angka-2025.html"
)
CIREBON_TABLE_URL = (
    "https://cirebonkota.bps.go.id/id/statistics-table/1/"
    "MTAwMjkjMQ%3D%3D/jumlah-penduduk-laju-pertumbuhan-penduduk-"
    "distribusi-persentase-penduduk-kepadatan-penduduk-rasio-jenis-"
    "kelamin-penduduk-menurut-kecamatan-di-kota-cirebon-2024-.html"
)
CIREBON_ROWS = [
    ("Harjamukti", 7418),
    ("Lemahwungkuk", 8200),
    ("Pekalipan", 18679),
    ("Kesambi", 9105),
    ("Kejaksan", 10538),
]

PUBLICATION_OVERRIDES = {
    "3202": {
        "year": 2024,
        "url": (
            "https://sukabumikab.bps.go.id/id/publication/2025/02/28/"
            "f8ba38bcb07aecd69affd6da/kabupaten-sukabumi-dalam-angka-2025.html"
        ),
        "title": "Kabupaten Sukabumi Dalam Angka 2025, Gambar 3.2 (data 2024)",
        "updated": "Publikasi dirilis 28 Februari 2025",
        # Urutan mengikuti baris tabel dinamis BPS (Ciemas s.d. Kabandungan).
        "densities": [
            190, 394, 326, 709, 350, 783, 438, 383, 145, 180, 482, 232,
            569, 395, 235, 1324, 362, 718, 566, 366, 397, 1104, 515, 649,
            2367, 3285, 1217, 1723, 1756, 870, 6203, 2250, 1993, 1809,
            1464, 1314, 843, 2678, 2216, 1292, 3330, 2062, 1115, 471,
            465, 422, 353,
        ],
    },
    "3213": {
        "year": 2024,
        "url": (
            "https://subangkab.bps.go.id/id/publication/2025/02/28/"
            "74921c29d690c53433cc4d23/subang-regency-in-figures-2025.html"
        ),
        "title": "Kabupaten Subang Dalam Angka 2025, Tabel 3.1.1 (data 2024)",
        "updated": "Publikasi dirilis 28 Februari 2025",
        "densities": [
            613, 371, 966, 480, 438, 899, 737, 388, 786, 2419, 766, 467,
            525, 880, 921, 777, 698, 1326, 685, 629, 701, 928, 715, 1055,
            2021, 735, 777, 954, 389, 641,
        ],
    },
    "3215": {
        "year": 2024,
        "url": (
            "https://karawangkab.bps.go.id/id/publication/2025/02/28/"
            "627a549169a6ff0ea5c94ae0/karawang-regency-in-figures-2025.html"
        ),
        "title": "Kabupaten Karawang Dalam Angka 2025, Tabel 3.1.1 (data 2024)",
        "updated": "Publikasi dirilis 28 Februari 2025",
        "densities": [
            428.91, 780.82, 390.98, 2875.41, 815.68, 2884.77, 3409.18,
            2704.07, 1119.98, 1511.40, 1046.41, 4321.49, 1048.87, 998.08,
            1273.32, 681.25, 2622.90, 5482.01, 4236.79, 1113.92, 719.28,
            1156.24, 3187.62, 1608.03, 1242.40, 652.15, 465.12, 691.71,
            1100.38, 617.61,
        ],
    },
}


def _load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_indonesian_integer(value: object) -> int:
    text = str(value).strip().replace("\u00a0", "")
    if not text or text in {"-", "–", "..."}:
        raise ValueError(f"Nilai kepadatan tidak valid: {value!r}")
    if "," in text:
        text = text.split(",", 1)[0]
    return int(text.replace(".", "").replace(" ", ""))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


def _ring_area_km2(ring: list[list[float]]) -> float:
    """Approximate geodesic area of one lon/lat ring on a sphere."""
    if len(ring) < 3:
        return 0.0
    total = 0.0
    pairs = zip(ring, ring[1:] + ring[:1])
    for (lon1, lat1), (lon2, lat2) in pairs:
        lon1 = math.radians(lon1)
        lon2 = math.radians(lon2)
        lat1 = math.radians(lat1)
        lat2 = math.radians(lat2)
        delta = lon2 - lon1
        if delta > math.pi:
            delta -= 2 * math.pi
        elif delta < -math.pi:
            delta += 2 * math.pi
        total += delta * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(total) * EARTH_RADIUS_KM**2 / 2


def _polygon_area_km2(polygon: list[list[list[float]]]) -> float:
    if not polygon:
        return 0.0
    exterior = _ring_area_km2(polygon[0])
    holes = sum(_ring_area_km2(ring) for ring in polygon[1:])
    return max(0.0, exterior - holes)


def _geometry_area_km2(geometry: dict) -> float:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    if geometry_type == "Polygon":
        return _polygon_area_km2(coordinates)
    if geometry_type == "MultiPolygon":
        return sum(_polygon_area_km2(polygon) for polygon in coordinates)
    return 0.0


def _subdistrict_areas(path: Path) -> dict[tuple[str, str], float]:
    boundary = json.loads(path.read_text(encoding="utf-8"))
    areas: dict[tuple[str, str], float] = {}
    for feature in boundary.get("features", []):
        properties = feature.get("properties") or {}
        key = (
            normalize_region_name(properties.get("WADMKK", "")),
            normalize_subdistrict_name(properties.get("WADMKC", "")),
        )
        area = _geometry_area_km2(feature.get("geometry") or {})
        if not all(key) or area <= 0:
            continue
        areas[key] = areas.get(key, 0.0) + area
    return areas


def validate_implied_population(
    rows: list[dict],
    boundary_path: Path = SUBDISTRICT_PATH,
    max_population: float = MAX_IMPLIED_SUBDISTRICT_POPULATION,
) -> dict[tuple[str, str], float]:
    """Reject density rows implying an impossible kecamatan population."""
    areas = _subdistrict_areas(boundary_path)
    implied_by_key: dict[tuple[str, str], float] = {}
    missing: list[str] = []
    invalid: list[tuple[dict, float, float]] = []
    for row in rows:
        key = (
            normalize_region_name(row["nama_kabupaten_kota"]),
            normalize_subdistrict_name(row["nama_kecamatan"]),
        )
        area = VALIDATION_AREA_OVERRIDES_KM2.get(key, areas.get(key))
        if area is None:
            missing.append(
                f"{row['nama_kabupaten_kota']} / {row['nama_kecamatan']}"
            )
            continue
        density = float(row["kepadatan_penduduk_per_km2"])
        implied = density * area
        implied_by_key[key] = implied
        if implied > max_population:
            invalid.append((row, area, implied))

    if missing:
        preview = "; ".join(missing[:5])
        raise ValueError(
            f"{len(missing)} baris tidak memiliki poligon kecamatan: {preview}"
        )
    if invalid:
        preview = "; ".join(
            f"{row['nama_kabupaten_kota']} / {row['nama_kecamatan']} "
            f"({float(row['kepadatan_penduduk_per_km2']):g} jiwa/km2 x "
            f"{area:.2f} km2 = {implied:,.0f} jiwa)"
            for row, area, implied in invalid[:5]
        )
        raise ValueError(
            f"{len(invalid)} nilai kepadatan mengimplikasikan lebih dari "
            f"{max_population:,.0f} penduduk dalam satu kecamatan: {preview}"
        )
    return implied_by_key


def _year(record: dict) -> int:
    matches = re.findall(r"\b(20\d{2})\b", str(record.get("title", "")))
    if not matches:
        matches = re.findall(r"year=(20\d{2})", str(record.get("url", "")))
    if not matches:
        raise ValueError(f"Tahun tidak ditemukan untuk {record.get('region')}")
    return int(matches[-1])


def _record_rows(record: dict) -> list[dict]:
    code = str(record["code"])
    raw_rows = record.get("rows", [])
    if len(raw_rows) < 2:
        raise ValueError(f"Tabel BPS kosong untuk {record.get('region')}")

    # Baris terakhir pada setiap tabel merupakan total kabupaten/kota.
    raw_rows = raw_rows[:-1]
    publication_override = PUBLICATION_OVERRIDES.get(code)
    if publication_override and len(publication_override["densities"]) != len(raw_rows):
        raise ValueError(f"Jumlah override publikasi tidak cocok untuk {record['region']}")
    density_index = 1 if code == "3276" else (3 if code == "3211" else 4)
    note = "Tabel statistik BPS."
    if code == "3211":
        note = (
            "Tabel statistik BPS; urutan sel HTML Sumedang menempatkan kepadatan "
            "pada kolom ke-4 dan persentase pada kolom ke-5."
        )
    elif code == "3276":
        note = "Tabel dinamis kepadatan penduduk BPS Kota Depok."

    if publication_override:
        note = (
            "Fallback publikasi resmi BPS karena tampilan tabel dinamis "
            "memotong sebagian nilai kepadatan ribuan."
        )

    return [
        {
            "kode_bps_kabupaten_kota": code,
            "provinsi": "Jawa Barat",
            "nama_kabupaten_kota": record["region"],
            "nama_kecamatan": str(row[0]).strip(),
            "tahun": (
                publication_override["year"] if publication_override else _year(record)
            ),
            "kepadatan_penduduk_per_km2": (
                publication_override["densities"][row_index]
                if publication_override
                else _parse_indonesian_integer(row[density_index])
            ),
            "sumber_bps_url": (
                publication_override["url"] if publication_override else record["url"]
            ),
            "sumber_bps_judul": (
                publication_override["title"]
                if publication_override
                else record["title"]
            ),
            "sumber_bps_diperbarui": (
                publication_override["updated"]
                if publication_override
                else record.get("updated", "")
            ),
            "catatan_sumber": note,
        }
        for row_index, row in enumerate(raw_rows)
    ]


def _cirebon_rows() -> list[dict]:
    title = "Kota Cirebon Dalam Angka 2025, Tabel 3.1.1 (data 2024)"
    note = (
        "Fallback publikasi PDF resmi BPS karena tabel statistik BPS tersedia "
        "tetapi isi/unduhan CSV-nya kosong. Tautan tabel: " + CIREBON_TABLE_URL
    )
    return [
        {
            "kode_bps_kabupaten_kota": "3274",
            "provinsi": "Jawa Barat",
            "nama_kabupaten_kota": "Kota Cirebon",
            "nama_kecamatan": district,
            "tahun": 2024,
            "kepadatan_penduduk_per_km2": density,
            "sumber_bps_url": CIREBON_PUBLICATION_URL,
            "sumber_bps_judul": title,
            "sumber_bps_diperbarui": "Publikasi dirilis 28 Februari 2025",
            "catatan_sumber": note,
        }
        for district, density in CIREBON_ROWS
    ]


def _scrape_rows(path: Path) -> list[dict]:
    """Rows from an audited province scrape (scripts/scrape_bps_population_density.py).

    The scraper reads BPS's own row payload, so each kecamatan already carries
    its official code and an unmangled value; the year chosen per region is the
    newest one that passed the audit, and the years it rejected are recorded.
    Every province scrape file shares one schema, so one reader serves all.
    """
    scrape = json.loads(path.read_text(encoding="utf-8"))

    rows: list[dict] = []
    for code, record in scrape.items():
        rejected = record.get("rejected") or []
        # A record may carry its own note (e.g. DKI regions sourced from the
        # publication); otherwise describe the statistics-table pull.
        note = record.get("catatan_sumber")
        if not note:
            note = "Tabel statistik BPS."
            if rejected:
                note += " Tahun lebih baru dilewati: " + "; ".join(rejected) + "."
        if record.get("status_sumber") == "fallback_publikasi_pdf":
            FALLBACK_CODES.add(code)
        for entry in record["rows"]:
            rows.append(
                {
                    "kode_bps_kabupaten_kota": code,
                    "provinsi": record["provinsi"],
                    "nama_kabupaten_kota": record["nama_kabupaten_kota"],
                    "nama_kecamatan": str(entry["kecamatan"]).strip(),
                    "tahun": int(record["tahun"]),
                    "kepadatan_penduduk_per_km2": entry["kepadatan"],
                    "sumber_bps_url": record["url"],
                    "sumber_bps_judul": record["judul"],
                    "sumber_bps_diperbarui": "",
                    "catatan_sumber": note,
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    records_by_code = {
        str(record["code"]): record for record in _load_json(PRIMARY_PATH)
    }
    for path in (RETRY_PATH, MISSING_PATH):
        for record in _load_json(path):
            code = str(record["code"])
            if code in OVERRIDE_CODES and record.get("rows"):
                records_by_code[code] = record

    rows: list[dict] = []
    for code in sorted(JAWA_BARAT_CODES - {"3274"}):
        rows.extend(_record_rows(records_by_code[code]))
    rows.extend(_cirebon_rows())
    for scrape_path in SCRAPE_PATHS:
        rows.extend(_scrape_rows(scrape_path))
    rows.sort(
        key=lambda item: (
            item["kode_bps_kabupaten_kota"],
            item["nama_kecamatan"].casefold(),
        )
    )

    # Validate before writing anything so a corrupt scrape cannot partially
    # replace the last known-good runtime dataset.
    validate_implied_population(rows)

    _write_csv(COMBINED_PATH, rows, COMBINED_FIELDS)

    region_codes = sorted({row["kode_bps_kabupaten_kota"] for row in rows})
    REGION_DIR.mkdir(parents=True, exist_ok=True)
    # Clear per-region files first so a regency that has since become a gap (its
    # only clean BPS year was later found to be damaged) does not leave a stale
    # CSV behind.
    for existing in REGION_DIR.glob("*.csv"):
        existing.unlink()
    for code in region_codes:
        region_rows = [
            row for row in rows if row["kode_bps_kabupaten_kota"] == code
        ]
        filename = f"{code}_{_slug(region_rows[0]['nama_kabupaten_kota'])}.csv"
        _write_csv(REGION_DIR / filename, region_rows, COMBINED_FIELDS)

    manifest = []
    for code in region_codes:
        region_rows = [
            row for row in rows if row["kode_bps_kabupaten_kota"] == code
        ]
        first = region_rows[0]
        manifest.append(
            {
                "kode_bps_kabupaten_kota": code,
                "provinsi": first["provinsi"],
                "nama_kabupaten_kota": first["nama_kabupaten_kota"],
                "tahun": first["tahun"],
                "jumlah_kecamatan": len(region_rows),
                "status_sumber": (
                    "fallback_publikasi_pdf"
                    if code == "3274"
                    or code in PUBLICATION_OVERRIDES
                    or code in FALLBACK_CODES
                    else "tabel_bps"
                ),
                "sumber_bps_url": first["sumber_bps_url"],
                "sumber_bps_judul": first["sumber_bps_judul"],
                "catatan_sumber": first["catatan_sumber"],
            }
        )
    _write_csv(
        MANIFEST_PATH,
        manifest,
        [
            "kode_bps_kabupaten_kota",
            "provinsi",
            "nama_kabupaten_kota",
            "tahun",
            "jumlah_kecamatan",
            "status_sumber",
            "sumber_bps_url",
            "sumber_bps_judul",
            "catatan_sumber",
        ],
    )

    if len(rows) != EXPECTED_SUBDISTRICTS:
        raise ValueError(
            f"Jumlah kecamatan {len(rows)}, seharusnya {EXPECTED_SUBDISTRICTS}"
        )
    print(f"Menulis {len(rows)} kecamatan ke {COMBINED_PATH}")
    print(f"Menulis {len(manifest)} sumber ke {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
