"""Tahap B source model from the merged Mw catalogue: the pure (testable) parts.

The steps run by ``scripts/build_source_model.py`` are:

1. Region: the box chosen in the app defines the hazard sites; earthquakes
   are taken from that box plus a buffer, because sources outside the box
   still shake sites inside it.
2. Catalogue selection from ``data/psha/catalog/mw_catalog.csv``.
3. Declustering (Gardner and Knopoff, 1974; HMTK).
4. Completeness (Stepp, 1972; HMTK automated version).
5. Gutenberg-Richter a and b (Weichert, 1980; HMTK) per depth layer.

This module holds the region, selection and table-building helpers; the
HMTK calls live in the script, which runs in the OpenQuake environment.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

KM_PER_DEGREE = 111.195
# Depth layers (km) of the background seismicity: the six gridded layers of
# GEM IDN v2024 (PuSGeN 2024), two in the shallow crust and four intraslab.
# Earthquakes deeper than 300 km are counted but not modelled, as in GEM.
DEFAULT_DEPTH_EDGES = (0.0, 25.0, 50.0, 100.0, 150.0, 200.0, 300.0)
# Grid spacing (degrees) and Gaussian smoothing distance (km) of the
# background seismicity (Frankel, 1995).
GRID_SPACING_DEG = 0.1
SMOOTHING_KM = 50.0
KERNEL_TRUNCATION = 3.0  # in smoothing distances
# Fewer events than this in a layer make its b-value unreliable.
MIN_EVENTS_FOR_B = 50
FLAG_LABELS = {0: "utama/independen", 1: "susulan", -1: "pendahulu"}


def expand_bounds(bounds: tuple, buffer_km: float) -> tuple:
    """``bounds`` (west, south, east, north) grown by ``buffer_km`` on every side."""
    west, south, east, north = bounds
    dlat = buffer_km / KM_PER_DEGREE
    widest = max(abs(south - dlat), abs(north + dlat))
    dlon = buffer_km / (KM_PER_DEGREE * max(math.cos(math.radians(min(widest, 89.0))), 1e-6))
    return (round(west - dlon, 4), round(max(-90.0, south - dlat), 4),
            round(east + dlon, 4), round(min(90.0, north + dlat), 4))


def region_warnings(source_bounds: tuple, download_region: dict) -> list[str]:
    """Sides of the source region that reach beyond the downloaded catalogue."""
    west, south, east, north = source_bounds
    sides = []
    if west < download_region["minlongitude"]:
        sides.append(f"barat ({west:.2f}° < {download_region['minlongitude']}°)")
    if east > download_region["maxlongitude"]:
        sides.append(f"timur ({east:.2f}° > {download_region['maxlongitude']}°)")
    if south < download_region["minlatitude"]:
        sides.append(f"selatan ({south:.2f}° < {download_region['minlatitude']}°)")
    if north > download_region["maxlatitude"]:
        sides.append(f"utara ({north:.2f}° > {download_region['maxlatitude']}°)")
    if not sides:
        return []
    return ["Wilayah sumber melewati batas katalog ISC yang diunduh di sisi "
            + ", ".join(sides) + "; gempa di luar batas itu tidak ikut dihitung."]


def select_catalog(
    catalog: pd.DataFrame,
    source_bounds: tuple,
    min_mw: float,
    include_unreviewed: bool = False,
) -> tuple[pd.DataFrame, dict]:
    """Events inside ``source_bounds`` with ``mw >= min_mw``; returns them and counts."""
    west, south, east, north = source_bounds
    inside = (catalog["longitude"].between(west, east)
              & catalog["latitude"].between(south, north))
    stats = {"in_source_region": int(inside.sum())}
    keep = inside & (catalog["mw"] >= min_mw)
    stats["mw_ge_min"] = int(keep.sum())
    stats["removed_unreviewed"] = 0
    if not include_unreviewed:
        unreviewed = keep & ~catalog["isc_reviewed_likely"].astype(bool)
        stats["removed_unreviewed"] = int(unreviewed.sum())
        keep &= ~unreviewed
    selected = catalog.loc[keep].copy()
    stats["selected"] = int(len(selected))
    return selected.reset_index(drop=True), stats


def layer_names(edges=DEFAULT_DEPTH_EDGES) -> list[str]:
    return [f"{edges[i]:g}-{edges[i + 1]:g} km" for i in range(len(edges) - 1)]


def depth_layer_labels(depth_km: pd.Series, edges=DEFAULT_DEPTH_EDGES) -> pd.Series:
    """Layer names such as "0-25 km".

    Missing depths go to the shallowest layer; depths below the last edge get
    ">300 km" (not modelled).
    """
    depth = depth_km.fillna(edges[0]).clip(lower=edges[0])
    labels = pd.cut(depth.clip(upper=edges[-1]), bins=list(edges), labels=layer_names(edges),
                    include_lowest=True).astype(str)
    return labels.where(depth <= edges[-1], f">{edges[-1]:g} km")


def gk_windows(mw) -> tuple[np.ndarray, np.ndarray]:
    """Gardner-Knopoff (1974) distance (km) and time (days) windows, as in HMTK."""
    mw = np.asarray(mw, dtype=float)
    km = np.power(10.0, 0.1238 * mw + 0.983)
    days = np.where(mw >= 6.5, np.power(10.0, 0.032 * mw + 2.7389),
                    np.power(10.0, 0.5409 * mw - 0.547))
    return km, days


def declustering_events(selected: pd.DataFrame, cluster: np.ndarray,
                        flag: np.ndarray) -> pd.DataFrame:
    """Per event: cluster number, role, own GK windows and the cluster's mainshock."""
    km, days = gk_windows(selected["mw"])
    frame = selected.assign(cluster_id=np.asarray(cluster).astype(int),
                            flag=np.asarray(flag).astype(int),
                            window_km=km.round(1), window_days=days.round(1))
    frame["role"] = frame["flag"].map(FLAG_LABELS)
    mains = frame.loc[(frame["cluster_id"] > 0) & (frame["flag"] == 0)]
    main_of = dict(zip(mains["cluster_id"], mains["isc_evid"]))
    frame["mainshock_evid"] = frame["cluster_id"].map(main_of)
    alone = frame["cluster_id"] == 0
    frame.loc[alone, "mainshock_evid"] = frame.loc[alone, "isc_evid"]
    frame["kept"] = frame["flag"] == 0
    return frame


def declustering_summary(events: pd.DataFrame, bin_width: float = 0.5) -> pd.DataFrame:
    """Counts kept and removed per Mw bin, plus a total row."""
    def row(label, group, low, high):
        n = len(group)
        removed = int((~group["kept"]).sum())
        return {"bin": label, "mw_min": round(low, 2), "mw_max": round(high, 2),
                "n_total": n, "n_kept": n - removed, "n_removed": removed,
                "n_aftershocks": int((group["flag"] == 1).sum()),
                "n_foreshocks": int((group["flag"] == -1).sum()),
                "removed_pct": round(100 * removed / max(n, 1), 1)}

    lower = np.floor(events["mw"] / bin_width + 1e-9) * bin_width
    rows = [row(f"{start:g}-{start + bin_width:g}", group, float(start),
                float(start) + bin_width) for start, group in events.groupby(lower)]
    rows.append(row("semua", events, float(events["mw"].min()), float(events["mw"].max())))
    return pd.DataFrame(rows)


CLUSTER_COLUMNS = ["cluster_id", "mainshock_evid", "mainshock_time", "mainshock_mw",
                   "longitude", "latitude", "depth_km", "n_events", "n_foreshocks",
                   "n_aftershocks", "span_days", "window_km", "window_days"]


def cluster_table(events: pd.DataFrame) -> pd.DataFrame:
    """One row per cluster: mainshock, size and time span, largest mainshock first."""
    clustered = events.loc[events["cluster_id"] > 0].copy()
    if clustered.empty:
        return pd.DataFrame(columns=CLUSTER_COLUMNS)
    clustered["t"] = pd.to_datetime(clustered["time"], utc=True, format="ISO8601")
    rows = []
    for cid, group in clustered.groupby("cluster_id"):
        main = group.loc[group["flag"] == 0].iloc[0]
        rows.append({
            "cluster_id": int(cid), "mainshock_evid": main["isc_evid"],
            "mainshock_time": main["time"], "mainshock_mw": main["mw"],
            "longitude": main["longitude"], "latitude": main["latitude"],
            "depth_km": main["depth_km"], "n_events": int(len(group)),
            "n_foreshocks": int((group["flag"] == -1).sum()),
            "n_aftershocks": int((group["flag"] == 1).sum()),
            "span_days": round((group["t"].max() - group["t"].min()).total_seconds() / 86400, 1),
            "window_km": main["window_km"], "window_days": main["window_days"],
        })
    return pd.DataFrame(rows, columns=CLUSTER_COLUMNS).sort_values(
        ["mainshock_mw", "n_events"], ascending=False).reset_index(drop=True)


def completeness_rows(table: np.ndarray, bin_width: float, mags: pd.Series,
                      years: pd.Series, end_year: int) -> pd.DataFrame:
    """Completeness table (complete-from year per magnitude bin) with event counts."""
    rows = []
    for year, mag in table:
        in_bin = (mags >= mag - 1e-9) & (mags < mag + bin_width - 1e-9)
        complete = in_bin & (years >= year)
        rows.append({"mw_min": round(float(mag), 2), "mw_max": round(float(mag) + bin_width, 2),
                     "complete_from_year": int(year),
                     "years_complete": int(end_year - year + 1),
                     "n_events_in_bin": int(in_bin.sum()),
                     "n_events_complete": int(complete.sum())})
    return pd.DataFrame(rows)


def stepp_rows(time_values: np.ndarray, sigma: np.ndarray, model_line: np.ndarray,
               magnitude_bin: np.ndarray, end_year: float) -> pd.DataFrame:
    """Stepp (1972) diagram data: sigma_lambda per time window and magnitude bin."""
    rows = []
    for j in range(len(magnitude_bin) - 1):
        for i, years in enumerate(time_values):
            rows.append({"mw_min": round(float(magnitude_bin[j]), 2),
                         "mw_max": round(float(magnitude_bin[j + 1]), 2),
                         "window_start_year": int(end_year - years),
                         "window_years": float(years),
                         "sigma_lambda": float(sigma[i, j]),
                         "poisson_line": float(model_line[i, j])})
    return pd.DataFrame(rows)


def completeness_period(mw: float, table: np.ndarray) -> float | None:
    """First complete year for magnitude ``mw`` from a (year, magnitude) table."""
    rows = table[table[:, 1] <= mw + 1e-9]
    if rows.size == 0:
        return None
    return float(rows[np.argmax(rows[:, 1]), 0])


def smoothed_grid(
    lons: np.ndarray,
    lats: np.ndarray,
    bounds: tuple,
    total_rate: float,
    spacing: float = GRID_SPACING_DEG,
    smoothing_km: float = SMOOTHING_KM,
) -> pd.DataFrame:
    """Smoothed seismicity (Frankel, 1995) on a grid over ``bounds``.

    Each epicentre is spread with a Gaussian kernel exp(-d^2 / c^2) (``c`` =
    ``smoothing_km``, truncated at ``KERNEL_TRUNCATION * c``) normalised so that
    every event contributes a total weight of one inside the grid. The cell
    weights are then scaled to ``total_rate`` (annual events >= Mmin of the
    layer). Returns ``longitude``, ``latitude``, ``count_raw`` (events whose
    epicentre falls in the cell), ``weight`` and ``annual_rate``; cells with no
    weight are dropped.
    """
    west, south, east, north = bounds
    glon = np.round(np.arange(np.ceil(west / spacing) * spacing, east + 1e-9, spacing), 4)
    glat = np.round(np.arange(np.ceil(south / spacing) * spacing, north + 1e-9, spacing), 4)
    grid_lon, grid_lat = (a.ravel() for a in np.meshgrid(glon, glat))
    weight = np.zeros(grid_lon.size)
    count = np.zeros(grid_lon.size, dtype=int)
    reach = KERNEL_TRUNCATION * smoothing_km
    for lon, lat in zip(np.asarray(lons, float), np.asarray(lats, float)):
        coslat = math.cos(math.radians(lat))
        dx = (grid_lon - lon) * KM_PER_DEGREE * coslat
        dy = (grid_lat - lat) * KM_PER_DEGREE
        d2 = dx * dx + dy * dy
        near = d2 <= reach * reach
        if not near.any():
            continue
        kernel = np.exp(-d2[near] / smoothing_km ** 2)
        weight[near] += kernel / kernel.sum()
        nearest = np.argmin(d2)
        if d2[nearest] <= (spacing * KM_PER_DEGREE) ** 2:
            count[nearest] += 1
    keep = weight > 0
    frame = pd.DataFrame({"longitude": grid_lon[keep], "latitude": grid_lat[keep],
                          "count_raw": count[keep], "weight": weight[keep]})
    total = frame["weight"].sum()
    frame["annual_rate"] = frame["weight"] * (total_rate / total if total > 0 else 0.0)
    return frame


def fmd_rows(mags: pd.Series, years: pd.Series, table: np.ndarray, end_year: int,
             dmag: float, mmin: float, a_value: float | None,
             b_value: float | None) -> pd.DataFrame:
    """Frequency-magnitude table: observed annual rates in each bin's complete period.

    Each bin counts only events after its completeness year and divides by
    that period's length. ``cum_rate_gr`` is the fitted Gutenberg-Richter
    annual rate ``10**(a - b*M)`` of events >= the bin's lower edge.
    """
    if mags.empty:
        return pd.DataFrame()
    edges = np.arange(mmin, mags.max() + dmag + 1e-9, dmag)
    rows = []
    for low in edges[:-1]:
        start = completeness_period(low, table)
        if start is None:
            continue
        span = end_year - start + 1
        in_bin = (mags >= low - 1e-9) & (mags < low + dmag - 1e-9) & (years >= start)
        rows.append({"mw_min": round(float(low), 2), "mw_center": round(float(low + dmag / 2), 3),
                     "complete_from_year": int(start), "years": int(span),
                     "n_obs": int(in_bin.sum()), "inc_rate": float(in_bin.sum()) / span})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["cum_rate_obs"] = out["inc_rate"][::-1].cumsum()[::-1]
    if a_value is not None and b_value is not None and np.isfinite(a_value):
        out["cum_rate_gr"] = 10.0 ** (a_value - b_value * out["mw_min"])
    else:
        out["cum_rate_gr"] = np.nan
    return out
