"""Magnitude homogenisation helpers for the PSHA catalogue (BMKG vs ISC).

- ``reference_mw``: picks one moment magnitude per ISC event from the agencies'
  reported magnitudes, most reliable source first.
- ``match_events``: pairs BMKG events with ISC events by origin time and
  epicentral distance.
- ``orthogonal_regression``: General Orthogonal Regression (Deming regression
  with an error-variance ratio), the usual choice for magnitude conversion
  because both magnitudes carry errors; ordinary least squares would be biased.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

EARTH_RADIUS_KM = 6371.0088

# (agency, magnitude types) in priority order. Mwp and Mw(mB) are excluded:
# they are proxies derived from P-wave or body-wave amplitudes, not moment
# tensor inversions.
MW_PRIORITY: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("GCMT", ("MW", "Mw")),
    # ISC-GEM Mw computed from a seismic moment (GCMT or the literature); its
    # proxy Mw (converted from MS/mb) is stored as "Mw(proxy)" and never used
    # as a reference.
    ("ISC-GEM", ("Mw",)),
    ("NEIC", ("Mww",)),
    ("NEIC", ("Mwc", "Mwb", "Mwr", "Mw", "MW")),
    ("GFZ", ("Mw", "MW")),
)
OTHER_MW_TYPES = ("MW", "Mw", "Mww")
EXCLUDED_MW_AGENCIES = ("DJA",)  # BMKG itself: its Mw is what is being calibrated


def reference_mw(magnitudes: pd.DataFrame) -> pd.DataFrame:
    """One reference Mw per ``isc_evid``.

    ``magnitudes`` has columns ``isc_evid``, ``agency``, ``mag_type`` and
    ``magnitude``. Returns ``isc_evid``, ``mw_ref``, ``mw_ref_agency``,
    ``mw_ref_type`` and ``mw_ref_rank`` (0 = GCMT, higher = less preferred).
    """
    frame = magnitudes.dropna(subset=["magnitude"]).copy()
    frame["rank"] = np.nan
    for rank, (agency, types) in enumerate(MW_PRIORITY):
        hit = (frame["agency"] == agency) & frame["mag_type"].isin(types)
        frame.loc[hit & frame["rank"].isna(), "rank"] = rank
    other = (frame["mag_type"].isin(OTHER_MW_TYPES)
             & ~frame["agency"].isin(EXCLUDED_MW_AGENCIES) & frame["rank"].isna())
    frame.loc[other, "rank"] = len(MW_PRIORITY)
    frame = frame.dropna(subset=["rank"]).sort_values(["isc_evid", "rank"], kind="stable")
    best = frame.drop_duplicates("isc_evid", keep="first")
    return pd.DataFrame({
        "isc_evid": best["isc_evid"].to_numpy(),
        "mw_ref": best["magnitude"].astype(float).to_numpy(),
        "mw_ref_agency": best["agency"].to_numpy(),
        "mw_ref_type": best["mag_type"].to_numpy(),
        "mw_ref_rank": best["rank"].astype(int).to_numpy(),
    })


def haversine_km(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(v, dtype=float))
                              for v in (lat1, lon1, lat2, lon2))
    a = (np.sin((lat2 - lat1) / 2) ** 2
         + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


@dataclass(frozen=True)
class MatchWindow:
    """Maximum origin-time and epicentre differences for a match."""

    seconds: float
    km: float


def _epoch_seconds(times: pd.Series) -> np.ndarray:
    return times.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy(
        "datetime64[ns]").astype("int64") / 1e9


def match_events(
    bmkg: pd.DataFrame,
    isc: pd.DataFrame,
    dja_window: MatchWindow = MatchWindow(5.0, 50.0),
    prime_window: MatchWindow = MatchWindow(16.0, 100.0),
) -> pd.DataFrame:
    """Pair BMKG events with ISC events.

    ``bmkg``: ``event_id``, ``time`` (tz-aware UTC), ``lat``, ``lon``.
    ``isc``: ``isc_evid``, ``time``, ``lat``, ``lon`` (prime origin) and
    ``dja_time``, ``dja_lat``, ``dja_lon`` (BMKG's origin as reported to ISC,
    may be missing).

    BMKG's own origin in the ISC Bulletin is compared first with a tight
    window; otherwise the ISC prime origin is used with a wider window. Each
    BMKG event takes the candidate with the smallest normalised distance
    (dt/seconds + d/km, DJA matches first), and each ISC event is kept for its
    best BMKG event only. Returns ``event_id``, ``isc_evid``, ``dt_s``,
    ``dist_km`` and ``matched_on`` ("DJA" or "prime").
    """
    columns = ["event_id", "isc_evid", "dt_s", "dist_km", "matched_on"]
    bm_t = _epoch_seconds(bmkg["time"])
    candidates = []
    for prefix, window, label in (("dja_", dja_window, "DJA"), ("", prime_window, "prime")):
        ref = isc.loc[isc[f"{prefix}time"].notna()].sort_values(f"{prefix}time")
        if ref.empty:
            continue
        ref_t = _epoch_seconds(ref[f"{prefix}time"])
        lo = np.searchsorted(ref_t, bm_t - window.seconds, side="left")
        hi = np.searchsorted(ref_t, bm_t + window.seconds, side="right")
        counts = hi - lo
        if counts.sum() == 0:
            continue
        bm_idx = np.repeat(np.arange(len(bmkg)), counts)
        ref_idx = np.concatenate([np.arange(a, b) for a, b in zip(lo, hi) if b > a])
        dt = np.abs(ref_t[ref_idx] - bm_t[bm_idx])
        dist = haversine_km(bmkg["lat"].to_numpy()[bm_idx], bmkg["lon"].to_numpy()[bm_idx],
                            ref[f"{prefix}lat"].to_numpy()[ref_idx],
                            ref[f"{prefix}lon"].to_numpy()[ref_idx])
        keep = dist <= window.km
        candidates.append(pd.DataFrame({
            "event_id": bmkg["event_id"].to_numpy()[bm_idx][keep],
            "isc_evid": ref["isc_evid"].to_numpy()[ref_idx][keep],
            "dt_s": dt[keep],
            "dist_km": dist[keep],
            "matched_on": label,
            "score": (dt[keep] / window.seconds + dist[keep] / window.km
                      + (0.0 if label == "DJA" else 2.0)),
        }))
    if not candidates:
        return pd.DataFrame(columns=columns)
    pairs = pd.concat(candidates, ignore_index=True).sort_values("score", kind="stable")
    pairs = pairs.drop_duplicates("event_id").drop_duplicates("isc_evid")
    return pairs[columns].reset_index(drop=True)


@dataclass(frozen=True)
class Conversion:
    """``mw = intercept + slope * m`` fitted on ``n`` pairs within [x_min, x_max]."""

    slope: float
    intercept: float
    n: int
    sigma: float
    x_min: float
    x_max: float
    eta: float

    def apply(self, magnitude):
        return self.intercept + self.slope * np.asarray(magnitude, dtype=float)


def orthogonal_regression(x, y, eta: float = 1.0) -> Conversion:
    """General Orthogonal Regression of ``y`` (Mw) on ``x``.

    ``eta`` is the ratio of the error variances var(e_y) / var(e_x); 1 gives
    orthogonal regression (Castellaro et al., 2006).
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3:
        raise ValueError("Paling sedikit 3 pasangan magnitudo diperlukan untuk regresi.")
    sxx = np.var(x, ddof=1)
    syy = np.var(y, ddof=1)
    sxy = np.cov(x, y, ddof=1)[0, 1]
    if sxy == 0:
        raise ValueError("Magnitudo tidak berkorelasi; regresi tidak terdefinisi.")
    slope = (syy - eta * sxx
             + math.sqrt((syy - eta * sxx) ** 2 + 4 * eta * sxy ** 2)) / (2 * sxy)
    intercept = float(np.mean(y) - slope * np.mean(x))
    residuals = y - (intercept + slope * x)
    return Conversion(float(slope), intercept, int(len(x)), float(np.std(residuals, ddof=2)),
                      float(x.min()), float(x.max()), eta)
