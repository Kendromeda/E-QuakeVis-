"""Download the ISC Bulletin (all reported magnitudes) for the BMKG catalogue area.

The ISC FDSN event service is queried in yearly (before 1964) or monthly
windows with ``includeallmagnitudes=true`` so that every agency's magnitude is
kept (GCMT MW, NEIC Mww, ISC mb, BMKG/DJA MLv, ...). Three stages cover as much
of the Bulletin as is useful (``STAGES``):

- ``recent_m4``: 2008-11 to today, M >= 4 (the BMKG catalogue period)
- ``history``: 1904 to 2008-10, every magnitude
- ``recent_small``: 2008-11 to today, M < 4

Each window's QuakeML is reduced to two CSV parts in ``data/psha/isc/parts/``;
windows that already have both parts are skipped, so the download can be
resumed. All parts are then combined (de-duplicated by ISC event id) into:

- ``data/psha/isc/isc_events.csv``: one row per ISC event with the preferred
  (prime) origin and, when BMKG reported one, the DJA origin
- ``data/psha/isc/isc_magnitudes.csv``: one row per reported magnitude
- ``data/psha/isc/manifest.json``: query, retrieval date, citation

Run ``python scripts/download_isc_bulletin.py [--stage history]``.

Data: International Seismological Centre (2026), On-line Bulletin,
https://doi.org/10.31905/D808B830. The Bulletin is free to use with citation.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "psha" / "isc"
PARTS_DIR = OUT_DIR / "parts"
SERVICE = "http://www.isc.ac.uk/fdsnws/event/1/query"
CITATION = ("International Seismological Centre (2026), On-line Bulletin, "
            "https://doi.org/10.31905/D808B830")
# BMKG catalogue extent (lat -11..6, lon 95..141) plus a margin for matching.
REGION = {"minlatitude": -11.5, "maxlatitude": 6.5,
          "minlongitude": 94.5, "maxlongitude": 141.5}
BMKG_AGENCY = "DJA"
EVENT_FIELDS = ["isc_evid", "time", "latitude", "longitude", "depth_km", "author",
                "dja_time", "dja_latitude", "dja_longitude", "dja_depth_km", "event_type"]
MAGNITUDE_FIELDS = ["isc_evid", "agency", "mag_type", "magnitude", "uncertainty",
                    "station_count", "is_preferred"]
USER_AGENT = "E-QuakeVis/1.0 (PSHA magnitude homogenisation)"


def _strip_namespaces(root: ET.Element) -> None:
    for element in root.iter():
        element.tag = element.tag.split("}")[-1]


def _text(element: ET.Element | None, path: str) -> str:
    found = element.find(path) if element is not None else None
    return (found.text or "").strip() if found is not None else ""


def _origin_fields(origin: ET.Element | None) -> tuple[str, str, str, str]:
    if origin is None:
        return "", "", "", ""
    depth = _text(origin, "depth/value")
    depth_km = f"{float(depth) / 1000:g}" if depth else ""
    return (_text(origin, "time/value"), _text(origin, "latitude/value"),
            _text(origin, "longitude/value"), depth_km)


def _agency(element: ET.Element) -> str:
    return _text(element, "creationInfo/agencyID") or _text(element, "creationInfo/author")


def parse_quakeml(payload: bytes) -> tuple[list[dict], list[dict]]:
    """Reduce an ISC QuakeML document to event and magnitude rows."""
    root = ET.fromstring(payload)
    _strip_namespaces(root)
    events, magnitudes = [], []
    for event in root.iter("event"):
        evid = event.get("publicID", "").rsplit("=", 1)[-1]
        origins = {origin.get("publicID"): origin for origin in event.findall("origin")}
        prime = origins.get(_text(event, "preferredOriginID"))
        dja = next((o for o in origins.values() if _agency(o) == BMKG_AGENCY), None)
        time_, lat, lon, depth = _origin_fields(prime)
        events.append(dict(zip(EVENT_FIELDS, (
            evid, time_, lat, lon, depth, _agency(prime) if prime is not None else "",
            *_origin_fields(dja), _text(event, "type")))))
        preferred_mag = _text(event, "preferredMagnitudeID")
        for magnitude in event.findall("magnitude"):
            agency = _agency(magnitude)
            if not agency:
                origin = origins.get(_text(magnitude, "originID"))
                agency = _agency(origin) if origin is not None else ""
            magnitudes.append(dict(zip(MAGNITUDE_FIELDS, (
                evid, agency, _text(magnitude, "type"), _text(magnitude, "mag/value"),
                _text(magnitude, "mag/uncertainty"), _text(magnitude, "stationCount"),
                int(magnitude.get("publicID") == preferred_mag)))))
    return events, magnitudes


def fetch(params: dict, attempts: int = 8) -> bytes:
    url = f"{SERVICE}?{urlencode(params)}"
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}),
                         timeout=600) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code == 204:  # FDSN: no events in this window
                return b""
            error: Exception = exc
            if exc.code == 429:  # Rate limited: back off for minutes, not seconds.
                retry_after = exc.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 600 * attempt
                print(f"  rate limited (429), waiting {wait // 60} min", flush=True)
                time.sleep(wait)
                continue
        except (URLError, TimeoutError, OSError) as exc:
            error = exc
        wait = 30 * attempt
        print(f"  attempt {attempt} failed ({error}); retrying in {wait} s", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"ISC query failed after {attempts} attempts: {url}")


def fetch_and_parse(params: dict, attempts: int = 4) -> tuple[list[dict], list[dict]]:
    """Fetch one window; re-fetch when the QuakeML is malformed.

    The service occasionally appends text after the closing ``</q:quakeml>``
    tag, so anything after it is dropped before parsing.
    """
    for attempt in range(1, attempts + 1):
        payload = fetch(params)
        if not payload.strip():
            return [], []
        end = payload.rfind(b"</q:quakeml>")
        if end >= 0:
            payload = payload[:end + len(b"</q:quakeml>")]
        try:
            return parse_quakeml(payload)
        except ET.ParseError as exc:
            print(f"  malformed QuakeML ({exc}), attempt {attempt}", flush=True)
            time.sleep(30 * attempt)
    raise RuntimeError(f"ISC returned malformed QuakeML {attempts} times: {params}")


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


@dataclass(frozen=True)
class Stage:
    """One query over a period, split into windows (one CSV part pair each)."""

    name: str
    start: date
    end: date | None  # None: today
    min_magnitude: float | None
    max_magnitude: float | None
    tag: str  # part-name suffix; "" keeps the original monthly M>=4 parts
    region: dict | None = None  # None: REGION
    yearly: bool = False  # yearly windows throughout (sparse margin bands)


# Bands around REGION so that a source region (a box plus a 300 km buffer, about
# 3 degrees) near the edge of Indonesia still has its earthquakes. Only M >= 4
# is needed there: those events feed source models, not magnitude calibration.
MARGIN_DEG = 3.5
EXTENDED_REGION = {"minlatitude": REGION["minlatitude"] - MARGIN_DEG,
                   "maxlatitude": REGION["maxlatitude"] + MARGIN_DEG,
                   "minlongitude": REGION["minlongitude"] - MARGIN_DEG,
                   "maxlongitude": REGION["maxlongitude"] + MARGIN_DEG}
MARGIN_BANDS = {
    "south": {**EXTENDED_REGION, "maxlatitude": REGION["minlatitude"]},
    "north": {**EXTENDED_REGION, "minlatitude": REGION["maxlatitude"]},
    "west": {**REGION, "minlongitude": EXTENDED_REGION["minlongitude"],
             "maxlongitude": REGION["minlongitude"]},
    "east": {**REGION, "minlongitude": REGION["maxlongitude"],
             "maxlongitude": EXTENDED_REGION["maxlongitude"]},
}

STAGES = (
    # BMKG catalogue period, the events that carry independent Mw.
    Stage("recent_m4", date(2008, 11, 1), None, 4.0, None, ""),
    # Everything before the BMKG catalogue, back to the start of the Bulletin.
    Stage("history", date(1904, 1, 1), date(2008, 11, 1), None, None, "_all"),
    # Small events in the BMKG period, to pair BMKG's own small-event magnitudes.
    Stage("recent_small", date(2008, 11, 1), None, None, 3.99, "_lt4"),
) + tuple(
    Stage(f"margin_{side}", date(1904, 1, 1), None, 4.0, None, f"_m4{side[0].upper()}",
          region=band, yearly=True)
    for side, band in MARGIN_BANDS.items()
)
MARGIN_STAGES = tuple(s.name for s in STAGES if s.name.startswith("margin_"))
# Before 1964 (pre-ISC) there are only tens of events a year in the region.
YEARLY_BEFORE = date(1964, 1, 1)


def windows(start: date, end: date, yearly: bool = False) -> list[tuple[date, date, str]]:
    """(start, end, label) windows: yearly before 1964 (or always), monthly afterwards."""
    result, current = [], start
    while current < end:
        if yearly or current < YEARLY_BEFORE:
            nxt, label = date(current.year + 1, 1, 1), f"{current:%Y}"
        else:
            nxt = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
            label = f"{current:%Y-%m}"
        result.append((current, min(nxt, end), label))
        current = nxt
    return result


def download_window(stage: Stage, start: date, end: date, label: str) -> tuple[int, int]:
    events_path = PARTS_DIR / f"{label}{stage.tag}_events.csv"
    mags_path = PARTS_DIR / f"{label}{stage.tag}_magnitudes.csv"
    if events_path.exists() and mags_path.exists():
        return -1, -1
    params: dict = {"starttime": start.isoformat(), "endtime": end.isoformat(),
                    **(stage.region or REGION)}
    if stage.min_magnitude is not None:
        params["minmagnitude"] = stage.min_magnitude
    if stage.max_magnitude is not None:
        params["maxmagnitude"] = stage.max_magnitude
    params.update({"includeallmagnitudes": "true", "format": "xml"})
    events, magnitudes = fetch_and_parse(params)
    # Magnitudes first: a window counts as done only once its events part exists.
    _write_csv(mags_path, MAGNITUDE_FIELDS, magnitudes)
    _write_csv(events_path, EVENT_FIELDS, events)
    return len(events), len(magnitudes)


def combine() -> tuple[int, int]:
    """Concatenate every part; stages may overlap, so duplicates are dropped."""
    import pandas as pd

    counts = []
    for kind, key in (("events", ["isc_evid"]), ("magnitudes", None)):
        parts = sorted(PARTS_DIR.glob(f"*_{kind}.csv"))
        frame = pd.concat([pd.read_csv(p, dtype=str, keep_default_na=False) for p in parts],
                          ignore_index=True)
        frame = frame.drop_duplicates(subset=key)
        if kind == "events":
            frame = frame.sort_values("time", kind="stable")
        frame.to_csv(OUT_DIR / f"isc_{kind}.csv", index=False)
        counts.append(len(frame))
    return counts[0], counts[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", choices=["all", "margins", *(s.name for s in STAGES)],
                        default="all", help="'margins' downloads the four margin bands")
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--pause", type=float, default=15.0, help="seconds between requests")
    args = parser.parse_args()

    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    stages = [s for s in STAGES if args.stage in ("all", s.name)
              or (args.stage == "margins" and s.name in MARGIN_STAGES)]
    for stage in stages:
        stage_windows = windows(stage.start, min(stage.end or args.end, args.end), stage.yearly)
        for index, (start, end, label) in enumerate(stage_windows, 1):
            n_events, n_mags = download_window(stage, start, end, label)
            if n_events < 0:
                continue
            print(f"[{stage.name} {index}/{len(stage_windows)}] {label}: {n_events} events, "
                  f"{n_mags} magnitudes", flush=True)
            time.sleep(args.pause)

    n_events, n_mags = combine()
    manifest = {
        "source": SERVICE,
        "citation": CITATION,
        "license": "ISC Bulletin: free to use with citation",
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        # Outer extent of every downloaded part; the margin bands hold M >= 4 only.
        "region": EXTENDED_REGION if any(PARTS_DIR.glob("*_m4*_events.csv")) else REGION,
        "core_region": REGION,
        "stages": [{"name": s.name, "start": s.start.isoformat(),
                    "end": (s.end or args.end).isoformat(),
                    "minmagnitude": s.min_magnitude, "maxmagnitude": s.max_magnitude,
                    "region": s.region or REGION}
                   for s in STAGES],
        "note": ("Magnitude limits are applied by the ISC service; every magnitude "
                 "reported for a selected event is kept and events are de-duplicated "
                 "by ISC event id. Events after the ISC review cut-off are unreviewed."),
        "num_events": n_events,
        "num_magnitudes": n_mags,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Done: {n_events} events, {n_mags} magnitudes in {OUT_DIR.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
