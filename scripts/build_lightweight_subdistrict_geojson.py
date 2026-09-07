"""Build the compact runtime copy of the subdistrict boundaries.

Merges every province snapshot the density layer covers into the single file
the application loads, keeping only the two properties the join needs. The
outlines are already generalised -- BIG does that when
scripts/fetch_kecamatan_boundaries.py takes the snapshot -- so this step only
trims and concatenates.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
SOURCE_PATHS = (
    ROOT_DIR / "data" / "kecamatan_jawa_barat.geojson",
    ROOT_DIR / "data" / "kecamatan_banten.geojson",
    ROOT_DIR / "data" / "kecamatan_jawa_tengah.geojson",
    ROOT_DIR / "data" / "kecamatan_jawa_timur.geojson",
    ROOT_DIR / "data" / "kecamatan_dki_jakarta.geojson",
    ROOT_DIR / "data" / "kecamatan_yogyakarta.geojson",
    ROOT_DIR / "data" / "kecamatan_sumatera_barat.geojson",
    ROOT_DIR / "data" / "kecamatan_jambi.geojson",
    ROOT_DIR / "data" / "kecamatan_sumatera_selatan.geojson",
    ROOT_DIR / "data" / "kecamatan_bengkulu.geojson",
    ROOT_DIR / "data" / "kecamatan_lampung.geojson",
)
OUTPUT_PATH = ROOT_DIR / "data" / "kecamatan_lightweight.geojson"
# 627 Jawa Barat + 155 Banten + 576 Jawa Tengah + 666 Jawa Timur + 44 DKI
# Jakarta + 78 Daerah Istimewa Yogyakarta (BIG carries four blank-name slivers
# in Jawa Timur, dropped below) = 2146 for Java. Plus the five Sumatra
# provinces' named kecamatan: 181 Sumatera Barat (BIG carries three blank-name
# slivers, dropped below), 144 Jambi, 241 Sumatera Selatan, 129 Bengkulu (one
# blank-name sliver dropped), and 229 Lampung = 924.
EXPECTED_FEATURES = 3070


def main() -> None:
    features: list[dict[str, Any]] = []
    for path in SOURCE_PATHS:
        source = json.loads(path.read_text(encoding="utf-8"))
        for feature in source.get("features", []):
            properties = feature.get("properties") or {}
            # BIG carries a few blank-name administrative slivers (water, unnamed
            # boundary fragments). They match no BPS kecamatan, so drop them.
            if not (properties.get("WADMKC") or "").strip():
                continue
            geometry = feature.get("geometry")
            if not (geometry or {}).get("coordinates"):
                raise ValueError(
                    f"{path.name}: {properties.get('WADMKC')} has no geometry"
                )
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "WADMKK": properties.get("WADMKK"),
                        "WADMKC": properties.get("WADMKC"),
                    },
                    "geometry": geometry,
                }
            )

    if len(features) != EXPECTED_FEATURES:
        raise ValueError(
            f"Expected {EXPECTED_FEATURES} features, found {len(features)}."
        )

    OUTPUT_PATH.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(features)} features to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
