from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

from shapely.geometry import shape
from shapely.ops import unary_union


ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT_DIR / "data" / "krb_gunung_api_indonesia_big.geojson"
LAYER_URL = (
    "https://kspservices.big.go.id/satupeta/rest/services/PUBLIK/"
    "SUMBER_DAYA_ALAM_DAN_LINGKUNGAN/MapServer/11/query"
)
KRB_LABELS = {1: "KRB I", 2: "KRB II", 3: "KRB III"}


def _display_name(raw_name: object) -> str:
    return f"Gunung {str(raw_name or '').strip().title()}"


def main() -> None:
    query = urlencode(
        {
            "where": "1=1",
            "outFields": "namobj,indga,thterbit,remark",
            "returnGeometry": "true",
            "outSR": "4326",
            "geometryPrecision": "5",
            "maxAllowableOffset": "0.0005",
            "f": "geojson",
        }
    )
    with urlopen(f"{LAYER_URL}?{query}", timeout=120) as response:
        geojson = json.load(response)

    features = geojson.get("features") or []
    by_name: dict[str, list[dict]] = {}
    for feature in features:
        properties = feature.get("properties") or {}
        name = str(properties.get("namobj") or "").strip()
        if name:
            by_name.setdefault(name, []).append(feature)

    centers: dict[str, tuple[float, float]] = {}
    for name, named_features in by_name.items():
        inner_features = [
            feature
            for feature in named_features
            if int((feature.get("properties") or {}).get("indga") or 0) == 3
        ] or named_features
        center = unary_union(
            [shape(feature["geometry"]) for feature in inner_features]
        ).centroid
        centers[name] = (float(center.y), float(center.x))

    enriched_features: list[dict] = []
    for feature in features:
        properties = dict(feature.get("properties") or {})
        name = str(properties.get("namobj") or "").strip()
        if not name or name not in centers:
            continue
        hazard_class = int(properties.get("indga") or 0)
        latitude, longitude = centers[name]
        properties.update(
            {
                "display_name": _display_name(name),
                "krb_label": KRB_LABELS.get(hazard_class, "KRB"),
                "volcano_latitude": round(latitude, 6),
                "volcano_longitude": round(longitude, 6),
            }
        )
        enriched_features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": feature["geometry"],
            }
        )

    enriched_features.sort(
        key=lambda feature: (
            feature["properties"]["display_name"].casefold(),
            int(feature["properties"].get("indga") or 0),
        )
    )
    output = {"type": "FeatureCollection", "features": enriched_features}
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"Wrote {len(enriched_features)} KRB polygons for {len(by_name)} volcanoes "
        f"to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
