from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from utils.magnitude_homogenization import (
    match_events,
    orthogonal_regression,
    reference_mw,
)


def utc(*values):
    return pd.to_datetime(list(values), utc=True)


def test_reference_mw_prefers_gcmt_then_neic_and_skips_proxies():
    mags = pd.DataFrame([
        ("1", "NEIC", "Mww", 6.1), ("1", "GCMT", "MW", 6.2), ("1", "DJA", "Mw(mB)", 6.0),
        ("2", "NEIC", "Mwp", 5.4), ("2", "NEIC", "Mwr", 5.2),
        ("3", "DJA", "MW", 4.9), ("3", "ISC", "mb", 4.7),
        ("4", "KMA", "Mw", 5.0),
        ("5", "ISC-GEM", "Mw(proxy)", 7.1), ("5", "ISC-GEM", "Mw", 7.3),
        ("5", "NEIC", "Mww", 7.2),
        ("6", "ISC-GEM", "Mw(proxy)", 6.4),
    ], columns=["isc_evid", "agency", "mag_type", "magnitude"])
    ref = reference_mw(mags).set_index("isc_evid")
    assert ref.loc["5", "mw_ref"] == 7.3 and ref.loc["5", "mw_ref_agency"] == "ISC-GEM"
    assert "6" not in ref.index  # a proxy Mw is not an independent reference
    assert ref.loc["1", "mw_ref"] == 6.2 and ref.loc["1", "mw_ref_agency"] == "GCMT"
    assert ref.loc["2", "mw_ref"] == 5.2 and ref.loc["2", "mw_ref_type"] == "Mwr"
    assert "3" not in ref.index  # only BMKG's own Mw and mb: no independent Mw
    assert ref.loc["4", "mw_ref_agency"] == "KMA"


def test_match_events_prefers_bmkg_origin_and_is_one_to_one():
    bmkg = pd.DataFrame({
        "event_id": ["a", "b", "c"],
        "time": utc("2020-01-01T00:00:00Z", "2020-01-01T00:00:03Z", "2020-01-02T00:00:00Z"),
        "lat": [-7.0, -7.0, 1.0],
        "lon": [110.0, 110.0, 120.0],
    })
    isc = pd.DataFrame({
        "isc_evid": ["x", "y"],
        "time": utc("2020-01-01T00:00:10Z", "2020-01-02T00:00:40Z"),
        "lat": [-7.1, 1.0],
        "lon": [110.1, 120.0],
        "dja_time": utc("2020-01-01T00:00:01Z", None),
        "dja_lat": [-7.0, np.nan],
        "dja_lon": [110.0, np.nan],
    })
    pairs = match_events(bmkg, isc).set_index("event_id")
    # a and b both fall in x's windows; a is closer in time to the DJA origin.
    assert pairs.loc["a", "isc_evid"] == "x"
    assert pairs.loc["a", "matched_on"] == "DJA"
    assert "b" not in pairs.index
    # c is 40 s from y: outside the 16 s prime window.
    assert "c" not in pairs.index


def test_match_events_uses_prime_origin_without_dja():
    bmkg = pd.DataFrame({"event_id": ["a"], "time": utc("2020-01-01T00:00:00Z"),
                         "lat": [-7.0], "lon": [110.0]})
    isc = pd.DataFrame({"isc_evid": ["x"], "time": utc("2020-01-01T00:00:12Z"),
                        "lat": [-7.5], "lon": [110.2],
                        "dja_time": pd.Series([pd.NaT], dtype="datetime64[ns, UTC]"),
                        "dja_lat": [np.nan], "dja_lon": [np.nan]})
    pairs = match_events(bmkg, isc)
    assert pairs.loc[0, "matched_on"] == "prime"
    assert pairs.loc[0, "dt_s"] == pytest.approx(12.0)
    assert pairs.loc[0, "dist_km"] == pytest.approx(59.8, abs=1.0)


def test_orthogonal_regression_recovers_known_line_with_errors_in_both():
    rng = np.random.default_rng(1)
    true = rng.uniform(4.0, 7.0, 4000)
    x = true + rng.normal(0, 0.2, true.size)
    y = 0.5 + 0.9 * true + rng.normal(0, 0.2 * 0.9, true.size)
    fit = orthogonal_regression(x, y, eta=0.81)
    assert fit.slope == pytest.approx(0.9, abs=0.02)
    assert fit.intercept == pytest.approx(0.5, abs=0.1)
    # Ordinary least squares is attenuated towards zero by the error in x.
    ols_slope = np.polyfit(x, y, 1)[0]
    assert ols_slope < fit.slope - 0.02
    assert fit.apply(5.0) == pytest.approx(fit.intercept + 5 * fit.slope)


def test_orthogonal_regression_needs_data():
    with pytest.raises(ValueError):
        orthogonal_regression([4.0, 5.0], [4.1, 5.1])
