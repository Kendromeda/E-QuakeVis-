from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.source_model import (
    cluster_table,
    completeness_period,
    completeness_rows,
    declustering_events,
    declustering_summary,
    depth_layer_labels,
    expand_bounds,
    fmd_rows,
    gk_windows,
    limit_years,
    region_warnings,
    select_catalog,
    smoothed_grid,
)

DOWNLOAD = {"minlatitude": -11.5, "maxlatitude": 6.5, "minlongitude": 94.5, "maxlongitude": 141.5}


def test_expand_bounds_adds_the_buffer_in_degrees():
    west, south, east, north = expand_bounds((120.0, -10.0, 122.0, -8.0), 111.195)
    assert south == pytest.approx(-11.0) and north == pytest.approx(-7.0)
    # Longitude degrees are shorter away from the equator, so they grow more.
    assert 120.0 - west > 1.0 and east - 122.0 == pytest.approx(120.0 - west)


def test_region_warnings_name_the_sides_beyond_the_download():
    assert region_warnings((100.0, -10.0, 120.0, 0.0), DOWNLOAD) == []
    notes = region_warnings((93.0, -13.0, 120.0, 0.0), DOWNLOAD)
    assert len(notes) == 1 and "barat" in notes[0] and "selatan" in notes[0]


def test_select_catalog_filters_region_magnitude_and_unreviewed():
    catalog = pd.DataFrame({
        "longitude": [120.0, 120.0, 120.0, 130.0],
        "latitude": [-9.0, -9.0, -9.0, -9.0],
        "mw": [5.0, 3.9, 5.5, 6.0],
        "isc_reviewed_likely": [True, True, False, True],
    })
    selected, stats = select_catalog(catalog, (119.0, -10.0, 121.0, -8.0), 4.0)
    assert list(selected["mw"]) == [5.0]
    assert stats == {"in_source_region": 3, "mw_ge_min": 2, "removed_unreviewed": 1,
                     "selected": 1}
    both, _ = select_catalog(catalog, (119.0, -10.0, 121.0, -8.0), 4.0, include_unreviewed=True)
    assert list(both["mw"]) == [5.0, 5.5]


def test_limit_years_keeps_the_inclusive_range_and_counts_the_rest():
    mains = pd.DataFrame({"year": [1950, 1964, 1990, 2010, 2020], "mw": [7.0, 5.0, 5.5, 6.0, 4.8]})
    kept, stats = limit_years(mains, 1964, 2010)
    assert list(kept["year"]) == [1964, 1990, 2010]
    assert stats == {"start_year": 1964, "end_year": 2010, "mainshocks_in": 5,
                     "removed_before_start": 1, "removed_after_end": 1, "mainshocks_used": 3}
    only_start, _ = limit_years(mains, start_year=2000)
    assert list(only_start["year"]) == [2010, 2020]
    everything, stats = limit_years(mains)
    assert len(everything) == 5 and stats["mainshocks_used"] == 5


def test_depth_layer_labels_put_missing_depth_in_the_shallowest_layer():
    labels = depth_layer_labels(pd.Series([10.0, 50.0, 75.0, np.nan, 800.0]),
                                (0.0, 50.0, 100.0, 700.0))
    assert list(labels) == ["0-50 km", "0-50 km", "50-100 km", "0-50 km", ">700 km"]


def test_smoothed_grid_conserves_the_total_rate_and_spreads_events():
    grid = smoothed_grid(np.array([120.0, 120.0]), np.array([-9.0, -9.0]),
                         (119.0, -10.0, 121.0, -8.0), total_rate=2.0, spacing=0.1,
                         smoothing_km=30.0)
    assert grid["annual_rate"].sum() == pytest.approx(2.0)
    assert grid["count_raw"].sum() == 2
    peak = grid.loc[grid["annual_rate"].idxmax()]
    assert (peak["longitude"], peak["latitude"]) == (120.0, -9.0)
    far = grid[(grid["longitude"] - 120.0).abs() > 1.0]
    assert far.empty  # beyond 3 smoothing distances (90 km) nothing is spread


def test_gk_windows_match_gardner_knopoff():
    km, days = gk_windows([5.0, 7.0])
    assert km[0] == pytest.approx(10 ** (0.1238 * 5 + 0.983))
    assert days[0] == pytest.approx(10 ** (0.5409 * 5 - 0.547))
    assert days[1] == pytest.approx(10 ** (0.032 * 7 + 2.7389))


def make_events():
    selected = pd.DataFrame({
        "isc_evid": ["m", "a1", "f1", "solo"],
        "time": ["2000-01-02T00:00:00Z", "2000-01-03T00:00:00Z", "2000-01-01T00:00:00Z",
                 "2010-01-01T00:00:00Z"],
        "longitude": [120.0, 120.1, 120.05, 125.0], "latitude": [-9.0] * 4,
        "depth_km": [20.0] * 4, "mw": [6.5, 5.0, 4.6, 5.2],
    })
    return declustering_events(selected, np.array([1, 1, 1, 0]), np.array([0, 1, -1, 0]))


def test_declustering_tables_report_roles_and_clusters():
    events = make_events()
    assert list(events["role"]) == ["utama/independen", "susulan", "pendahulu",
                                    "utama/independen"]
    assert list(events["mainshock_evid"]) == ["m", "m", "m", "solo"]
    assert list(events["kept"]) == [True, False, False, True]
    summary = declustering_summary(events).set_index("bin")
    assert summary.loc["semua", "n_removed"] == 2 and summary.loc["semua", "n_kept"] == 2
    assert summary.loc["5-5.5", "n_aftershocks"] == 1
    clusters = cluster_table(events)
    assert len(clusters) == 1
    row = clusters.iloc[0]
    assert row["mainshock_evid"] == "m" and row["n_aftershocks"] == 1
    assert row["n_foreshocks"] == 1 and row["span_days"] == 2.0


def test_completeness_and_fmd_tables():
    table = np.array([[2000.0, 4.5], [1960.0, 5.0], [1900.0, 6.0]])
    assert completeness_period(4.7, table) == 2000.0
    assert completeness_period(5.5, table) == 1960.0
    assert completeness_period(4.0, table) is None
    mags = pd.Series([4.6, 4.6, 5.1, 6.2, 4.6])
    years = pd.Series([2010, 2020, 1970, 1950, 1990])  # the 1990 M4.6 is before completeness
    rows = completeness_rows(table, 0.5, mags, years, 2024)
    assert list(rows["n_events_complete"]) == [2, 1, 1]
    assert list(rows["years_complete"]) == [25, 65, 125]
    fmd = fmd_rows(mags, years, table, 2024, 0.5, 4.5, 6.0, 1.0)
    first = fmd.iloc[0]
    assert first["n_obs"] == 2 and first["years"] == 25 and first["inc_rate"] == pytest.approx(0.08)
    assert first["cum_rate_gr"] == pytest.approx(10 ** (6.0 - 4.5))
    assert fmd["cum_rate_obs"].is_monotonic_decreasing
