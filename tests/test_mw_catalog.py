from __future__ import annotations

import pandas as pd
import pytest

from utils.mw_catalog import (
    build_catalog,
    clean_events,
    conversions_table,
    iscgem_only_events,
    load_conversions,
    mw_scales,
    remove_duplicates,
    select_mw,
    summarize_by_period,
    summarize_sources,
)

REGION = {"minlatitude": -11.5, "maxlatitude": 6.5, "minlongitude": 94.5, "maxlongitude": 141.5}
SUMMARY = {"isc_internal_conversions_gor_eta_1": {
    "ISC mb": {"pairs": 100, "slope": 1.2, "intercept": -1.0, "sigma": 0.25,
               "valid_range": [4.0, 7.0]},
    "NEIC mb": {"pairs": 100, "slope": 1.0, "intercept": 0.0, "sigma": 0.25,
                "valid_range": [4.0, 7.0]},
    "ISC MS": {"pairs": 100, "slope": 0.7, "intercept": 2.0, "sigma": 0.2,
               "valid_range": [3.5, 8.5]},
    "DJA MLv": {"pairs": 100, "slope": 0.9, "intercept": 0.5, "sigma": 0.25,
                "valid_range": [3.0, 7.0]},
    "DJA MS": {"pairs": 0},
}}


def mags(rows):
    return pd.DataFrame(rows, columns=["isc_evid", "agency", "mag_type", "magnitude"])


def test_load_conversions_maps_agencies_and_caps_ms():
    rules = {rule.label: rule for rule in load_conversions(SUMMARY)}
    assert "DJA MS" not in rules  # no fitted equation
    assert rules["NEIC mb"].agencies == ("NEIC", "NEIS", "USCGS")
    assert rules["DJA MLv"].agencies == ("DJA",) and rules["DJA MLv"].mag_types == ("MLv",)
    assert rules["ISC MS"].x_max == 6.1 and "bilinear" in rules["ISC MS"].note


def test_select_mw_prefers_direct_then_proxy_then_converted():
    rules = load_conversions(SUMMARY)
    frame = mags([
        ("1", "GCMT", "MW", 6.2), ("1", "ISC", "mb", 5.8),
        ("2", "ISC-GEM", "Mw(proxy)", 7.1), ("2", "ISC", "MS", 6.0),
        ("3", "ISC", "mb", 5.0),
        ("4", "MAN", "md", 5.5),  # no calibrated scale
    ])
    out = select_mw(frame, rules).set_index("isc_evid")
    assert out.loc["1", "mw_method"] == "direct" and out.loc["1", "mw"] == 6.2
    assert out.loc["2", "mw_method"] == "iscgem_proxy" and out.loc["2", "mw"] == 7.1
    assert out.loc["3", "mw_method"] == "converted"
    assert out.loc["3", "mw"] == pytest.approx(1.2 * 5.0 - 1.0)
    assert "4" not in out.index


def test_select_mw_averages_conversions_with_inverse_variance_weights():
    rules = load_conversions(SUMMARY)
    # ISC mb 5.0 -> 5.0 and NEIS (old NEIC code) mb 5.4 -> 5.4, equal sigma.
    out = select_mw(mags([("1", "ISC", "mb", 5.0), ("1", "NEIS", "mb", 5.4)]), rules)
    row = out.iloc[0]
    assert row["mw"] == pytest.approx(5.2)
    assert row["mw_sigma"] == pytest.approx(0.25 / 2 ** 0.5)
    assert row["mw_source"] == "ISC mb 5.00 + NEIC mb 5.40"


def test_select_mw_skips_magnitudes_outside_the_fitted_range():
    rules = load_conversions(SUMMARY)
    # MS 7.0 is above the capped MS range; mb 3.5 is below the mb range.
    out = select_mw(mags([("1", "ISC", "MS", 7.0), ("2", "ISC", "mb", 3.5)]), rules)
    assert out.empty


def test_clean_events_drops_outside_and_non_earthquakes_and_flags_fixed_depth():
    events = pd.DataFrame({
        "isc_evid": ["1", "2", "3", "4"],
        "time": ["2000-01-01T00:00:00Z", "2000-01-02T00:00:00.12Z",
                 "2000-01-03T00:00:00Z", "2000-01-04T00:00:00Z"],
        "latitude": [-8.0, -8.0, 30.0, -8.0],
        "longitude": [120.0, 120.0, 120.0, 120.0],
        "depth_km": [10.0, 55.5, 20.0, 15.0],
        "event_type": ["earthquake", "earthquake", "earthquake", "mining explosion"],
    })
    frame, stats = clean_events(events, REGION)
    assert list(frame["isc_evid"]) == ["1", "2"]
    assert list(frame["depth_fixed"]) == [True, False]
    assert stats["removed_outside_region"] == 1 and stats["removed_not_earthquake"] == 1


def test_remove_duplicates_keeps_the_better_magnitude():
    catalog = pd.DataFrame({
        "isc_evid": ["a", "b", "c"],
        "time": pd.to_datetime(["2020-01-01T00:00:00Z", "2020-01-01T00:00:05Z",
                                "2020-01-01T00:10:00Z"], utc=True),
        "latitude": [-8.0, -8.3, -8.0],
        "longitude": [120.0, 120.1, 120.0],
        "mw": [6.0, 6.1, 5.0],
        "mw_method": ["converted", "direct", "converted"],
        "mw_sigma": [0.2, 0.1, 0.2],
        "mw_source": ["x", "y", "z"],
    })
    kept, dupes = remove_duplicates(catalog)
    assert list(kept["isc_evid"]) == ["b", "c"]
    assert dupes.iloc[0]["isc_evid"] == "a" and dupes.iloc[0]["duplicate_of"] == "b"


def test_iscgem_only_events_adds_missing_events_inside_region():
    gem = pd.DataFrame({
        "eventid": [1, 2, 3],
        "time": pd.to_datetime(["1906-06-01T04:30:50Z"] * 3, utc=True),
        "lat": [-2.5, -2.5, 40.0], "lon": [138.5, 138.5, 138.5], "depth": [35.0] * 3,
    })
    events = pd.DataFrame({"isc_evid": ["2"]})
    extra = iscgem_only_events(gem, events, REGION)
    assert list(extra["isc_evid"]) == ["1"] and extra.iloc[0]["author"] == "ISC-GEM"


def test_build_catalog_end_to_end_with_summaries():
    rules = load_conversions(SUMMARY)
    events = pd.DataFrame({
        "isc_evid": ["1", "2", "3"],
        "time": ["1990-01-01T00:00:00Z", "2015-01-01T00:00:00Z", "2026-01-01T00:00:00Z"],
        "latitude": [-8.0, -7.0, -6.0], "longitude": [120.0, 121.0, 122.0],
        "depth_km": [33.0, 100.0, 20.0], "author": ["ISC"] * 3,
        "event_type": ["earthquake"] * 3,
    })
    frame = mags([("1", "ISC", "mb", 5.0), ("2", "GCMT", "MW", 6.5), ("3", "MAN", "md", 4.8)])
    catalog, stats, excluded, dupes = build_catalog(
        events, frame, rules, REGION, 4.0, pd.Timestamp("2026-10-10", tz="UTC"))
    assert list(catalog["isc_evid"]) == ["1", "2"]
    assert stats["without_mw"] == 1 and stats["excluded_with_large_magnitude"] == 1
    assert excluded.iloc[0]["reason"] == "tidak ada skala terkalibrasi ke Mw"
    assert dupes.empty and catalog["isc_reviewed_likely"].all()
    by_period = summarize_by_period(catalog).set_index("period_start")
    assert by_period.loc[2010, "n_direct"] == 1 and by_period.loc[1990, "n_converted"] == 1
    assert set(summarize_sources(catalog)["mw_source"]) == {"GCMT MW", "ISC mb"}
    used = conversions_table(rules, catalog).set_index("conversion")["n_events_used"]
    assert used["ISC mb"] == 1 and used["NEIC mb"] == 0


def test_mw_scales_strips_values():
    assert mw_scales(pd.Series(["ISC mb 4.60 + NEIC mb 4.70"]))[0] == "ISC mb + NEIC mb"
