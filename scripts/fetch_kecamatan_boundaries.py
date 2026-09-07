"""Snapshot BIG kecamatan boundaries for a province into data/.

Rebuild-time helper: the application reads the committed snapshot, never the
BIG service. Re-run only when refreshing the snapshot.

BIG is asked to generalise the outlines server-side (`maxAllowableOffset`).
Banten's full-resolution coastline and islands weigh ~52 MB for 155 kecamatan,
which is far more detail than a choropleth needs; the tolerance matches the one
build_lightweight_subdistrict_geojson.py applies to the Jawa Barat snapshot.
"""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SERVICE_URL = (
    "https://geoservices.big.go.id/gis/rest/services/BAPANAS/"
    "Batas_Administrasi/MapServer/1/query"
)
PAGE_SIZE = 100
SIMPLIFY_TOLERANCE_DEGREES = 0.001
COORDINATE_PRECISION = 6

TARGETS = {
    "jawa_barat": (
        "Jawa Barat",
        ROOT_DIR / "data" / "kecamatan_jawa_barat.geojson",
        627,
    ),
    "banten": ("Banten", ROOT_DIR / "data" / "kecamatan_banten.geojson", 155),
    "dki": (
        "DKI Jakarta",
        ROOT_DIR / "data" / "kecamatan_dki_jakarta.geojson",
        44,
    ),
    "jawa_tengah": (
        "Jawa Tengah",
        ROOT_DIR / "data" / "kecamatan_jawa_tengah.geojson",
        576,
    ),
    "jawa_timur": (
        "Jawa Timur",
        ROOT_DIR / "data" / "kecamatan_jawa_timur.geojson",
        670,
    ),
    "yogyakarta": (
        "Daerah Istimewa Yogyakarta",
        ROOT_DIR / "data" / "kecamatan_yogyakarta.geojson",
        78,
    ),
    "sumatera_barat": (
        "Sumatera Barat",
        ROOT_DIR / "data" / "kecamatan_sumatera_barat.geojson",
        184,
    ),
    "jambi": ("Jambi", ROOT_DIR / "data" / "kecamatan_jambi.geojson", 144),
    "sumatera_selatan": (
        "Sumatera Selatan",
        ROOT_DIR / "data" / "kecamatan_sumatera_selatan.geojson",
        241,
    ),
    "bengkulu": ("Bengkulu", ROOT_DIR / "data" / "kecamatan_bengkulu.geojson", 130),
    "lampung": ("Lampung", ROOT_DIR / "data" / "kecamatan_lampung.geojson", 229),
}


def _query(province: str, offset: int) -> dict:
    params = {
        "where": f"WADMPR='{province}'",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
        "resultOffset": offset,
        "resultRecordCount": PAGE_SIZE,
        "orderByFields": "KDCPUM",
        "maxAllowableOffset": SIMPLIFY_TOLERANCE_DEGREES,
        "geometryPrecision": COORDINATE_PRECISION,
    }
    url = SERVICE_URL + "?" + urllib.parse.urlencode(params)
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(url, timeout=180, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_province(province: str) -> list[dict]:
    features: list[dict] = []
    offset = 0
    while True:
        batch = _query(province, offset).get("features", [])
        features.extend(batch)
        if len(batch) < PAGE_SIZE:
            return features
        offset += PAGE_SIZE


def main(keys: list[str] | None = None) -> None:
    selected = keys or list(TARGETS)
    for key in selected:
        province, path, expected = TARGETS[key]
        features = fetch_province(province)
        if len(features) != expected:
            raise ValueError(
                f"{province}: expected {expected} kecamatan, got {len(features)}"
            )
        path.write_text(
            json.dumps(
                {"type": "FeatureCollection", "features": features},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"{province}: wrote {len(features)} kecamatan to {path.name}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:] or None)
