"""One moment magnitude (Mw) per event: the merged catalogue for Tahap B.

Each ISC event gets a single Mw, from the most reliable source available:

1. ``direct``: an Mw from a moment tensor (``reference_mw``: GCMT > ISC-GEM >
   NEIC Mww > NEIC other Mw > GFZ > other agencies, never BMKG's own Mw).
2. ``iscgem_proxy``: ISC-GEM's own Mw estimate converted from MS/mb, used for
   events before moment tensors (mostly before 1976).
3. ``converted``: other magnitudes converted with General Orthogonal
   Regressions fitted inside the ISC Bulletin
   (``data/psha/isc/magnitude_conversion_summary.json``), each only inside the
   magnitude range it was fitted on. When several conversions apply, their
   results are averaged with inverse-variance weights (1/sigma^2), so no single
   scale (for example a local MLv) decides alone.

Events without any of these keep no Mw and are counted, not dropped silently.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from utils.magnitude_homogenization import haversine_km, reference_mw

# Typical 1-sigma of a moment-tensor Mw when the agency reports none.
DIRECT_SIGMA = 0.1
# ISC-GEM proxy Mw without a reported uncertainty.
PROXY_SIGMA = 0.2
# A single straight line underestimates Mw for large MS: the MS-Mw relation is
# bilinear with a break near MS 6.1 (Scordilis, 2006, J. Seismol. 10:225-236).
MS_CONVERSION_MAX = 6.1
# Depths that ISC and contributing agencies use when the depth was fixed, not
# solved; these events are flagged, not removed.
FIXED_DEPTHS_KM = (0.0, 10.0, 33.0)
# Labels in the summary JSON -> (agency codes, magnitude types in the ISC
# Bulletin). NEIC reported under USCGS (1960s-1973) and NEIS before the
# current code; the mb is the same scale.
CONVERSION_SOURCES = {
    "ISC MS": (("ISC",), ("MS", "Ms")),
    "ISC mb": (("ISC",), ("mb",)),
    "NEIC mb": (("NEIC", "NEIS", "USCGS"), ("mb",)),
}
METHOD_ORDER = ("direct", "iscgem_proxy", "converted")
# Left-out events with a reported magnitude at least this large are listed for review.
EXCLUDED_REVIEW_MAG = 4.5
# Two catalogue entries this close in time and space are one earthquake reported
# twice (common before ISC review merges contributions); the ISC prime-origin
# matching window of scripts/match_bmkg_isc.py.
DUPLICATE_SECONDS = 16.0
DUPLICATE_KM = 100.0
# ISC reviews its Bulletin about 24 months behind real time; newer events may
# still change or be duplicated.
REVIEW_LAG_MONTHS = 24
CATALOG_COLUMNS = ["isc_evid", "time", "year", "longitude", "latitude", "depth_km",
                   "depth_fixed", "mw", "mw_sigma", "mw_method", "mw_source", "mag_original",
                   "origin_author", "isc_reviewed_likely"]


@dataclass(frozen=True)
class ConversionRule:
    """``Mw = intercept + slope * m`` for ``agencies``/``mag_types`` within [x_min, x_max]."""

    label: str
    agencies: tuple
    mag_types: tuple
    slope: float
    intercept: float
    sigma: float
    pairs: int
    x_min: float
    x_max: float
    note: str = ""

    def apply(self, magnitude):
        return self.intercept + self.slope * np.asarray(magnitude, dtype=float)


def load_conversions(summary: dict) -> list[ConversionRule]:
    """Conversion rules from ``magnitude_conversion_summary.json``.

    Uses the ISC-internal regressions (they pair magnitudes of the same ISC
    event, so they apply to the Bulletin's own magnitudes in every year).
    Entries without a fitted equation (too few pairs) are skipped.
    """
    rules = []
    for label, entry in summary["isc_internal_conversions_gor_eta_1"].items():
        if "slope" not in entry:
            continue
        if label.startswith("DJA "):
            agencies, mag_types = ("DJA",), (label[4:],)
        elif label in CONVERSION_SOURCES:
            agencies, mag_types = CONVERSION_SOURCES[label]
        else:
            continue
        x_min, x_max = (float(v) for v in entry["valid_range"])
        note = ""
        if any(t.upper() == "MS" for t in mag_types) and x_max > MS_CONVERSION_MAX:
            x_max = MS_CONVERSION_MAX
            note = f"dibatasi MS <= {MS_CONVERSION_MAX} (hubungan MS-Mw bilinear)"
        rules.append(ConversionRule(label, tuple(agencies), tuple(mag_types), float(entry["slope"]),
                                    float(entry["intercept"]), float(entry["sigma"]),
                                    int(entry["pairs"]), x_min, x_max, note))
    return rules


def select_mw(magnitudes: pd.DataFrame, rules: list[ConversionRule]) -> pd.DataFrame:
    """One Mw per ``isc_evid``.

    ``magnitudes``: ``isc_evid``, ``agency``, ``mag_type``, ``magnitude`` and
    optionally ``uncertainty``. Returns ``isc_evid``, ``mw``, ``mw_sigma``,
    ``mw_method``, ``mw_source`` and ``mag_original`` (the magnitude the Mw came
    from).
    """
    columns = ["isc_evid", "mw", "mw_sigma", "mw_method", "mw_source", "mag_original"]
    mags = magnitudes.assign(magnitude=pd.to_numeric(magnitudes["magnitude"], errors="coerce"))
    mags = mags.dropna(subset=["magnitude"])
    if "uncertainty" not in mags:
        mags = mags.assign(uncertainty=np.nan)
    mags = mags.assign(uncertainty=pd.to_numeric(mags["uncertainty"], errors="coerce"))
    parts = []

    ref = reference_mw(mags)
    if not ref.empty:
        unc = mags.merge(ref[["isc_evid", "mw_ref_agency", "mw_ref_type"]],
                         left_on=["isc_evid", "agency", "mag_type"],
                         right_on=["isc_evid", "mw_ref_agency", "mw_ref_type"])
        unc = unc.drop_duplicates("isc_evid").set_index("isc_evid")["uncertainty"]
        parts.append(pd.DataFrame({
            "isc_evid": ref["isc_evid"],
            "mw": ref["mw_ref"],
            "mw_sigma": ref["isc_evid"].map(unc).fillna(DIRECT_SIGMA).to_numpy(),
            "mw_method": "direct",
            "mw_source": ref["mw_ref_agency"] + " " + ref["mw_ref_type"],
            "mag_original": ref["mw_ref"],
        }))

    proxy = mags[(mags["agency"] == "ISC-GEM") & (mags["mag_type"] == "Mw(proxy)")]
    proxy = proxy.drop_duplicates("isc_evid")
    parts.append(pd.DataFrame({
        "isc_evid": proxy["isc_evid"],
        "mw": proxy["magnitude"],
        "mw_sigma": proxy["uncertainty"].fillna(PROXY_SIGMA),
        "mw_method": "iscgem_proxy",
        "mw_source": "ISC-GEM Mw(proxy)",
        "mag_original": proxy["magnitude"],
    }))

    converted = []
    for rule in rules:
        hit = mags[mags["agency"].isin(rule.agencies) & mags["mag_type"].isin(rule.mag_types)
                   & mags["magnitude"].between(rule.x_min, rule.x_max)]
        # One value per event and rule (an agency may report the scale twice).
        hit = hit.drop_duplicates("isc_evid")
        if hit.empty:
            continue
        converted.append(pd.DataFrame({
            "isc_evid": hit["isc_evid"],
            "mw_one": rule.apply(hit["magnitude"]),
            "weight": 1.0 / rule.sigma ** 2,
            "label": rule.label + " " + hit["magnitude"].map("{:.2f}".format),
        }))
    if converted:
        conv = pd.concat(converted, ignore_index=True).sort_values(["isc_evid", "label"])
        conv["weighted"] = conv["mw_one"] * conv["weight"]
        grouped = conv.groupby("isc_evid", sort=False)
        total = grouped["weight"].sum()
        parts.append(pd.DataFrame({
            "isc_evid": total.index,
            "mw": (grouped["weighted"].sum() / total).to_numpy(),
            "mw_sigma": (1.0 / np.sqrt(total)).to_numpy(),
            "mw_method": "converted",
            # e.g. "ISC mb 4.60 + NEIC mb 4.70"
            "mw_source": grouped["label"].agg(" + ".join).to_numpy(),
            "mag_original": np.nan,
        }))

    parts = [p[columns] for p in parts if not p.empty]
    if not parts:
        return pd.DataFrame(columns=columns)
    allmw = pd.concat(parts, ignore_index=True)
    allmw["order"] = allmw["mw_method"].map({m: i for i, m in enumerate(METHOD_ORDER)})
    allmw = allmw.sort_values(["isc_evid", "order", "mw_sigma"], kind="stable")
    best = allmw.drop_duplicates("isc_evid", keep="first")
    return best[columns].reset_index(drop=True)


def clean_events(events: pd.DataFrame, region: dict) -> tuple[pd.DataFrame, dict]:
    """Earthquakes whose own (prime) epicentre lies inside ``region``.

    ``region`` has ``minlatitude``, ``maxlatitude``, ``minlongitude`` and
    ``maxlongitude`` (the download box). ISC may return events whose prime
    origin is elsewhere because another agency's origin fell inside the box.
    Adds ``depth_fixed`` (depth equals a common fixed value or is missing).
    Returns the events and counts of what was removed.
    """
    frame = events.copy()
    frame["time"] = pd.to_datetime(frame["time"], utc=True, format="ISO8601", errors="coerce")
    stats = {"events_in": int(len(frame))}
    bad_time = frame["time"].isna()
    not_quake = frame["event_type"].fillna("earthquake") != "earthquake"
    outside = ~(frame["latitude"].between(region["minlatitude"], region["maxlatitude"])
                & frame["longitude"].between(region["minlongitude"], region["maxlongitude"]))
    stats.update({"removed_bad_time": int(bad_time.sum()),
                  "removed_not_earthquake": int((not_quake & ~bad_time).sum()),
                  "removed_outside_region": int((outside & ~not_quake & ~bad_time).sum())})
    frame = frame.loc[~(bad_time | not_quake | outside)].copy()
    depth = pd.to_numeric(frame["depth_km"], errors="coerce")
    frame["depth_km"] = depth
    frame["depth_fixed"] = depth.isna() | depth.isin(FIXED_DEPTHS_KM)
    stats["events_kept"] = int(len(frame))
    stats["depth_fixed"] = int(frame["depth_fixed"].sum())
    return frame, stats


def iscgem_only_events(gem: pd.DataFrame, events: pd.DataFrame, region: dict) -> pd.DataFrame:
    """ISC-GEM events inside ``region`` that the ISC download does not contain.

    ``gem`` is ``utils.iscgem.load_iscgem`` output (``eventid``, ``time``,
    ``lat``, ``lon``, ``depth``). Returns rows shaped like ``isc_events.csv``
    (author "ISC-GEM"); their magnitudes join on the same event id.
    """
    inside = (gem["lat"].between(region["minlatitude"], region["maxlatitude"])
              & gem["lon"].between(region["minlongitude"], region["maxlongitude"]))
    ids = gem["eventid"].astype(str)
    extra = gem.loc[inside & ~ids.isin(events["isc_evid"].astype(str))]
    extra = extra.drop_duplicates("eventid")
    return pd.DataFrame({
        "isc_evid": extra["eventid"].astype(str),
        "time": pd.to_datetime(extra["time"], utc=True).dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "latitude": extra["lat"],
        "longitude": extra["lon"],
        "depth_km": extra["depth"],
        "author": "ISC-GEM",
        "event_type": "earthquake",
    })


def remove_duplicates(catalog: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop second reports of one earthquake (within ``DUPLICATE_SECONDS`` and ``DUPLICATE_KM``).

    ``catalog`` has ``isc_evid``, ``time`` (tz-aware), ``latitude``,
    ``longitude``, ``mw_method`` and ``mw_sigma``, sorted by time. Of each pair
    the entry with the better Mw (method order, then smaller sigma) is kept.
    Returns the catalogue and the removed rows with ``duplicate_of``.
    """
    seconds = catalog["time"].astype("int64").to_numpy() / 1e9
    order = catalog["mw_method"].map({m: i for i, m in enumerate(METHOD_ORDER)}).to_numpy()
    sigma = catalog["mw_sigma"].to_numpy()
    lat, lon = catalog["latitude"].to_numpy(), catalog["longitude"].to_numpy()
    ids = catalog["isc_evid"].to_numpy()
    drop, duplicate_of = set(), {}
    for i in range(len(catalog)):
        if ids[i] in drop:
            continue
        j = i + 1
        while j < len(catalog) and seconds[j] - seconds[i] <= DUPLICATE_SECONDS:
            if (ids[j] not in drop
                    and haversine_km(lat[i], lon[i], lat[j], lon[j]) <= DUPLICATE_KM):
                worse, better = (j, i) if (order[i], sigma[i]) <= (order[j], sigma[j]) else (i, j)
                drop.add(ids[worse])
                duplicate_of[ids[worse]] = ids[better]
                if worse == i:
                    break
            j += 1
    removed = catalog["isc_evid"].isin(drop)
    dupes = catalog.loc[removed].assign(duplicate_of=lambda d: d["isc_evid"].map(duplicate_of))
    return catalog.loc[~removed], dupes


def build_catalog(
    events: pd.DataFrame,
    magnitudes: pd.DataFrame,
    rules: list[ConversionRule],
    region: dict,
    min_mw: float,
    retrieved: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, dict, pd.DataFrame, pd.DataFrame]:
    """The merged Mw catalogue: cleaned events joined with their selected Mw.

    Keeps events with ``mw >= min_mw``, without duplicate reports. Returns the
    catalogue (sorted by time, columns ``CATALOG_COLUMNS``), processing counts,
    the events left out although some agency reported a magnitude >=
    ``EXCLUDED_REVIEW_MAG`` (for review), and the removed duplicates.
    ``retrieved`` (download time) marks events newer than ``REVIEW_LAG_MONTHS``
    as probably not reviewed by ISC yet.
    """
    frame, stats = clean_events(events, region)
    mags = magnitudes[magnitudes["isc_evid"].isin(frame["isc_evid"])]
    mw = select_mw(mags, rules)
    merged = frame.merge(mw, on="isc_evid", how="left")
    stats["without_mw"] = int(merged["mw"].isna().sum())
    stats["below_min_mw"] = int((merged["mw"] < min_mw).sum())
    excluded = _excluded_large(merged.loc[~(merged["mw"] >= min_mw)], mags)
    stats["excluded_with_large_magnitude"] = int(len(excluded))
    catalog = merged.loc[merged["mw"] >= min_mw].sort_values("time", kind="stable")
    catalog, dupes = remove_duplicates(catalog.reset_index(drop=True))
    stats["removed_duplicates"] = int(len(dupes))
    cutoff = (retrieved - pd.DateOffset(months=REVIEW_LAG_MONTHS)) if retrieved is not None \
        else None
    reviewed = (catalog["time"] < cutoff) if cutoff is not None \
        else pd.Series(True, index=catalog.index)
    stats["probably_unreviewed"] = int((~reviewed).sum())
    catalog = pd.DataFrame({
        "isc_evid": catalog["isc_evid"].astype(str),
        "time": catalog["time"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f").str[:-4] + "Z",
        "year": catalog["time"].dt.year,
        "longitude": catalog["longitude"].round(4),
        "latitude": catalog["latitude"].round(4),
        "depth_km": catalog["depth_km"].round(1),
        "depth_fixed": catalog["depth_fixed"],
        "mw": catalog["mw"].round(2),
        "mw_sigma": catalog["mw_sigma"].round(3),
        "mw_method": catalog["mw_method"],
        "mw_source": catalog["mw_source"],
        "mag_original": catalog["mag_original"].round(2),
        "origin_author": catalog["author"],
        "isc_reviewed_likely": reviewed,
    })[CATALOG_COLUMNS].reset_index(drop=True)
    stats["events_in_catalog"] = int(len(catalog))
    stats["min_mw"] = min_mw
    dupes = pd.DataFrame({
        "isc_evid": dupes["isc_evid"].astype(str),
        "time": dupes["time"].dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        "longitude": dupes["longitude"].round(4),
        "latitude": dupes["latitude"].round(4),
        "mw": dupes["mw"].round(2),
        "mw_source": dupes["mw_source"],
        "duplicate_of": dupes["duplicate_of"].astype(str),
    })
    return catalog, stats, excluded, dupes


def _excluded_large(left_out: pd.DataFrame, mags: pd.DataFrame) -> pd.DataFrame:
    """Left-out events whose largest reported magnitude is >= ``EXCLUDED_REVIEW_MAG``."""
    numeric = mags.assign(magnitude=pd.to_numeric(mags["magnitude"], errors="coerce"))
    numeric = numeric[numeric["isc_evid"].isin(left_out["isc_evid"])
                      & (numeric["magnitude"] >= EXCLUDED_REVIEW_MAG)]
    largest = numeric.sort_values("magnitude", kind="stable").drop_duplicates(
        "isc_evid", keep="last")
    out = left_out.merge(largest[["isc_evid", "agency", "mag_type", "magnitude"]],
                         on="isc_evid")
    reason = np.where(out["mw"].isna(), "tidak ada skala terkalibrasi ke Mw",
                      "Mw hasil konversi/langsung di bawah batas")
    return pd.DataFrame({
        "isc_evid": out["isc_evid"].astype(str),
        "time": out["time"].dt.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        "longitude": out["longitude"].round(4),
        "latitude": out["latitude"].round(4),
        "depth_km": out["depth_km"].round(1),
        "largest_agency": out["agency"],
        "largest_mag_type": out["mag_type"],
        "largest_magnitude": out["magnitude"],
        "mw": out["mw"].round(2),
        "mw_source": out["mw_source"],
        "reason": reason,
    }).sort_values("largest_magnitude", ascending=False, kind="stable").reset_index(drop=True)


def summarize_by_period(catalog: pd.DataFrame, period_years: int = 10) -> pd.DataFrame:
    """Event counts per period and Mw origin, with counts at or above Mw 5, 6 and 7."""
    frame = catalog.assign(period_start=(catalog["year"] // period_years) * period_years)
    rows = []
    for start, group in frame.groupby("period_start"):
        row = {"period_start": int(start), "period_end": int(start) + period_years - 1,
               "n_total": int(len(group))}
        for method in METHOD_ORDER:
            row[f"n_{method}"] = int((group["mw_method"] == method).sum())
        for level in (5, 6, 7):
            row[f"n_mw_ge_{level}"] = int((group["mw"] >= level).sum())
        row["mw_max"] = float(group["mw"].max())
        rows.append(row)
    return pd.DataFrame(rows)


def mw_scales(source: pd.Series) -> pd.Series:
    """``mw_source`` without values: "ISC mb 4.60 + NEIC mb 4.70" -> "ISC mb + NEIC mb"."""
    return source.str.replace(r" -?\d+\.\d+", "", regex=True)


def summarize_sources(catalog: pd.DataFrame) -> pd.DataFrame:
    """Events per Mw source (scale combination), with the Mw range."""
    frame = catalog.assign(mw_source=mw_scales(catalog["mw_source"]))
    grouped = frame.groupby(["mw_method", "mw_source"])["mw"]
    out = grouped.agg(n_events="size", mw_min="min", mw_median="median", mw_max="max")
    out = out.reset_index()
    out["share_pct"] = (100 * out["n_events"] / max(1, len(catalog))).round(2)
    out["method_order"] = out["mw_method"].map({m: i for i, m in enumerate(METHOD_ORDER)})
    out = out.sort_values(["method_order", "n_events"], ascending=[True, False], kind="stable")
    return out.drop(columns="method_order").reset_index(drop=True)


def conversions_table(rules: list[ConversionRule], catalog: pd.DataFrame) -> pd.DataFrame:
    """The conversion equations and how many catalogue events each one contributed to."""
    sources = catalog.loc[catalog["mw_method"] == "converted", "mw_source"]
    parts = mw_scales(sources).str.split(" + ", regex=False).explode()
    used = parts.value_counts()
    return pd.DataFrame([{
        "conversion": rule.label,
        "equation": f"Mw = {rule.slope:.4f} * m {'+' if rule.intercept >= 0 else '-'} "
                    f"{abs(rule.intercept):.4f}",
        "sigma": rule.sigma,
        "pairs": rule.pairs,
        "valid_min": rule.x_min,
        "valid_max": rule.x_max,
        "n_events_used": int(used.get(rule.label, 0)),
        "note": rule.note,
    } for rule in rules])
