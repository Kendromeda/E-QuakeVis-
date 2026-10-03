"""Build Indonesia's subduction trenches from the USGS Slab2 trench axis.

Rebuild-time helper. The application reads the committed
``data/indonesia_trench_slab2.geojson`` snapshot, never this network source.

Source: Hayes (2018), Slab2 -- A Comprehensive Subduction Zone Geometry Model,
USGS data release (DOI 10.5066/F7PV6JNV). The global trench axis ships as
``slab2code/library/forplotting/trenches_usgs_2017.csv`` (columns
``lon,lat,az,bound,slab``). Four Slab2 trench axes reach Indonesia and are kept
as separate trench features, each clipped to its Indonesian extent:

- ``sum`` (Sunda) -- Sumatra-Java-Nusa Tenggara and the Banda arc. The northern
  tail of this region is the India-Eurasia continental collision (Indo-Burman /
  Himalaya, lat > 14 N), which is not a trench and is dropped.
- ``phi`` (Philippine) -- kept south of ~8.5 N: the segment off Morotai,
  Talaud, and Sulawesi Utara. Further north it runs along the Philippines.
- ``png`` (New Guinea) -- the trench off the north coast of Papua; the few
  vertices east of the Indonesia-PNG border (lon > 141.5) are dropped.
- ``sul`` (North Sulawesi) -- the Molucca Sea/Sunda (MS/SU) trench north of
  Sulawesi's northern arm. It is missing from ``trenches_usgs_2017.csv`` but
  present in the companion ``trenches_usgs_2017_depths2.csv`` of the same
  Slab2 release, which is read for this region only.

Slab2 publishes no trench axis for the Halmahera (``hal``) model: its shallowest
grid node is ~33 km, below the Molucca Sea collision complex, so there is no
Slab2 trench line to draw there.

Pass ``--source-dir`` to read both CSVs from a local folder instead of GitHub.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.request
from pathlib import Path
from typing import Callable

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT_DIR / "data" / "indonesia_trench_slab2.geojson"

SOURCE_BASE_URL = (
    "https://raw.githubusercontent.com/usgs/slab2/master/"
    "slab2code/library/forplotting/"
)
PRIMARY_SOURCE = "trenches_usgs_2017.csv"
DEPTH_SOURCE = "trenches_usgs_2017_depths2.csv"
USER_AGENT = "Mozilla/5.0 (compatible; DatabaseInteraktif/1.0)"

# A step larger than this (degrees) between consecutive kept vertices is a
# digitising artefact, not the trench, and breaks the line.
MAX_STEP_DEGREES = 1.0


# One entry per Slab2 region that reaches Indonesia: display name and the clip
# that keeps only its Indonesian extent.
TRENCHES: list[dict[str, object]] = [
    {
        "slab": "sum",
        "name": "Palung Sunda-Banda",
        "keep": lambda lon, lat: lat <= 14.0,
    },
    {
        "slab": "phi",
        "name": "Palung Filipina (Maluku Utara-Sulawesi Utara)",
        "keep": lambda lon, lat: lat <= 8.5,
    },
    {
        "slab": "png",
        "name": "Palung Papua Utara (New Guinea)",
        "keep": lambda lon, lat: lon <= 141.5,
    },
    {
        "slab": "sul",
        "name": "Palung Sulawesi Utara",
        "keep": lambda lon, lat: True,
        "source_file": DEPTH_SOURCE,
    },
]


def _fetch_rows(file_name: str, source_dir: Path | None) -> list[dict[str, str]]:
    if source_dir is not None:
        text = (source_dir / file_name).read_text(encoding="utf-8", errors="ignore")
    else:
        request = urllib.request.Request(
            SOURCE_BASE_URL + file_name, headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            text = response.read().decode("utf-8", "ignore")
    return list(csv.DictReader(io.StringIO(text)))


def _clip_line(
    rows: list[dict[str, str]],
    slab: str,
    keep: Callable[[float, float], bool],
) -> list[list[float]]:
    line: list[list[float]] = []
    previous: list[float] | None = None
    for row in rows:
        if row.get("slab") != slab:
            continue
        longitude = float(row["lon"])
        latitude = float(row["lat"])
        if not keep(longitude, latitude):
            continue
        point = [round(longitude, 4), round(latitude, 4)]
        if previous is not None:
            if abs(point[0] - previous[0]) + abs(point[1] - previous[1]) > MAX_STEP_DEGREES:
                continue
        line.append(point)
        previous = point
    return line


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Indonesia trench GeoJSON.")
    parser.add_argument("--source-dir", type=Path, default=None)
    args = parser.parse_args()
    rows_by_file: dict[str, list[dict[str, str]]] = {}
    features = []
    for trench in TRENCHES:
        source_file = str(trench.get("source_file", PRIMARY_SOURCE))
        if source_file not in rows_by_file:
            rows_by_file[source_file] = _fetch_rows(source_file, args.source_dir)
        rows = rows_by_file[source_file]
        line = _clip_line(rows, trench["slab"], trench["keep"])  # type: ignore[arg-type]
        if len(line) < 2:
            raise SystemExit(f"trench {trench['slab']} came back empty")
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "Name": trench["name"],
                    "Type": "Trench",
                    "slab_region": trench["slab"],
                    "source": (
                        f"USGS Slab2 (Hayes 2018), {source_file}, "
                        f"region '{trench['slab']}'"
                    ),
                    "doi": "10.5066/F7PV6JNV",
                },
                "geometry": {"type": "LineString", "coordinates": line},
            }
        )
        lons = [p[0] for p in line]
        lats = [p[1] for p in line]
        print(
            f"{trench['slab']}: {len(line):3d} vertices  "
            f"lon {min(lons):7.2f}..{max(lons):7.2f} lat {min(lats):7.2f}..{max(lats):7.2f}  "
            f"-> {trench['name']}"
        )

    OUTPUT_PATH.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    total = sum(len(f["geometry"]["coordinates"]) for f in features)
    print(f"Wrote {len(features)} trenches, {total} vertices to {OUTPUT_PATH.name}")


if __name__ == "__main__":
    main()
