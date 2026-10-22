from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image
import pytest

from utils.map_builder import hazard_image_data_url
from utils.psha_results import (
    HAZARD_BREAKS_G,
    PSHAResultError,
    hazard_classes,
    hazard_grid,
    hazard_legend_labels,
    list_psha_runs,
    load_hazard_curves,
    load_hazard_map,
    nearest_site,
    return_period,
)

SITES = [(106.8, -6.2), (106.9, -6.2), (106.8, -6.3)]


def write_run(root: Path, run_id: str = "uji", **manifest_changes) -> Path:
    run_dir = root / run_id
    run_dir.mkdir(parents=True)
    manifest = {
        "run_id": run_id, "openquake_version": "3.26.2",
        "model": {"name": "GEM IDN", "model_version": "v2017.1.0", "license": "CC BY-SA 4.0",
                  "source": "https://example.org", "authors": "Irsyam dkk.",
                  "documentation": "https://example.org/doc"},
        "region": {"provinces": ["DKI Jakarta"], "spacing_degrees": 0.1},
        "num_sites": len(SITES),
        "site_condition": {"vs30_m_s": 760.0},
        "investigation_time_years": 50.0, "poes": [0.1, 0.02], "imts": ["PGA"],
        "outputs": {"hazard_map.csv": "x", "hazard_curves-PGA.csv": "y"},
        **manifest_changes,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pd.DataFrame({
        "lon": [s[0] for s in SITES], "lat": [s[1] for s in SITES],
        "PGA-0.1": [0.30, 0.32, 0.28], "PGA-0.02": [0.55, 0.60, 0.50],
    }).to_csv(run_dir / "hazard_map.csv", index=False)
    pd.DataFrame({
        "lon": [s[0] for s in SITES], "lat": [s[1] for s in SITES],
        "poe-0.1": [0.5, 0.6, 0.4], "poe-0.5": [0.05, 0.06, 0.04], "poe-1.0": [0.0, 0.01, 0.0],
    }).to_csv(run_dir / "hazard_curves-PGA.csv", index=False)
    return run_dir


def test_return_periods_match_standard_hazard_levels():
    assert round(return_period(0.1, 50)) == 475
    assert round(return_period(0.02, 50)) == 2475
    with pytest.raises(PSHAResultError):
        return_period(1.0, 50)


def test_lists_finished_runs_and_skips_dry_runs(tmp_path):
    write_run(tmp_path, "kecil")
    (tmp_path / "belum_dihitung").mkdir()
    (tmp_path / "belum_dihitung" / "job.ini").write_text("[general]")
    runs = list_psha_runs(tmp_path)
    assert [run.run_id for run in runs] == ["kecil"]
    assert [(c.column, c.return_period_years) for c in runs[0].columns] == [
        ("PGA-0.1", 475), ("PGA-0.02", 2475)]
    assert list_psha_runs(tmp_path / "tidak_ada") == []


def test_incomplete_manifest_is_rejected(tmp_path):
    run_dir = write_run(tmp_path)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    del manifest["imts"]
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(PSHAResultError, match="imts"):
        list_psha_runs(tmp_path)


def test_hazard_map_is_validated_against_manifest(tmp_path):
    run = list_psha_runs(write_run(tmp_path).parent)[0]
    frame = load_hazard_map(run)
    assert frame["PGA-0.02"].tolist() == [0.55, 0.60, 0.50]

    pd.DataFrame({"lon": [106.8], "lat": [-6.2], "PGA-0.1": [0.3], "PGA-0.02": [0.5]}).to_csv(
        run.path / "hazard_map.csv", index=False)
    with pytest.raises(PSHAResultError, match="3"):
        load_hazard_map(run)


def test_negative_or_missing_hazard_values_are_rejected(tmp_path):
    run = list_psha_runs(write_run(tmp_path).parent)[0]
    frame = pd.read_csv(run.path / "hazard_map.csv")
    frame.loc[1, "PGA-0.1"] = -0.1
    frame.to_csv(run.path / "hazard_map.csv", index=False)
    with pytest.raises(PSHAResultError, match="PGA-0.1"):
        load_hazard_map(run)


def test_hazard_curves_levels_and_probabilities(tmp_path):
    run = list_psha_runs(write_run(tmp_path).parent)[0]
    levels, curves = load_hazard_curves(run, "PGA")
    assert levels.tolist() == [0.1, 0.5, 1.0]
    assert curves.shape == (3, 5)
    with pytest.raises(PSHAResultError, match="SA"):
        load_hazard_curves(run, "SA(1.0)")


def test_grid_places_each_site_in_its_cell_north_up():
    frame = pd.DataFrame({"lon": [106.8, 106.9, 106.8], "lat": [-6.2, -6.2, -6.3],
                          "v": [1.0, 2.0, 3.0]})
    values, bounds = hazard_grid(frame, "v", 0.1)
    assert values.shape == (2, 2)
    assert values[0].tolist() == [1.0, 2.0]          # northern row (-6.2)
    assert values[1, 0] == 3.0 and np.isnan(values[1, 1])
    assert bounds == pytest.approx((106.75, -6.35, 106.95, -6.15))


def test_grid_rejects_sites_off_the_regular_grid():
    frame = pd.DataFrame({"lon": [106.8, 106.83], "lat": [-6.2, -6.2], "v": [1.0, 2.0]})
    with pytest.raises(PSHAResultError):
        hazard_grid(frame, "v", 0.1)


def test_classes_follow_breaks_and_mark_empty_cells():
    breaks = HAZARD_BREAKS_G["PGA"]
    classes = hazard_classes(np.array([0.0, 0.099, 0.1, 0.99, 1.5, np.nan]), breaks)
    assert classes.tolist() == [0, 0, 1, 8, 10, -1]
    labels = hazard_legend_labels(breaks)
    assert len(labels) == len(breaks)
    assert labels[1] == "0,1 – 0,2" and labels[-1] == "≥ 1,3"


def test_every_imt_has_one_colour_per_class():
    from utils.psha_results import HAZARD_COLORS

    for breaks in HAZARD_BREAKS_G.values():
        assert len(breaks) == len(HAZARD_COLORS)


def test_smooth_grid_interpolates_between_sites_and_clips_to_region():
    from shapely.geometry import box

    from utils.psha_results import smooth_hazard_grid

    # Two sites 0.1 deg apart on one row: 0.2 g and 0.4 g.
    values = np.array([[0.2, 0.4]])
    bounds = (106.75, -6.25, 106.95, -6.15)
    fine, out_bounds = smooth_hazard_grid(values, bounds, 0.1, max_cells=20)
    assert out_bounds == bounds
    row = fine[fine.shape[0] // 2]
    assert row[0] == pytest.approx(0.2) and row[-1] == pytest.approx(0.4)
    assert np.all(np.diff(row) >= -1e-12)  # rises smoothly from west to east
    assert 0.25 < np.nanmedian(row) < 0.35
    # Only the western half lies in the region polygon.
    clipped, _ = smooth_hazard_grid(values, bounds, 0.1, [box(106.75, -6.25, 106.85, -6.15)],
                                    max_cells=20)
    assert np.isfinite(clipped[:, 0]).all() and np.isnan(clipped[:, -1]).all()


def test_smooth_grid_fills_small_gaps_but_not_far_from_sites():
    from utils.psha_results import smooth_hazard_grid

    values = np.full((5, 5), np.nan)
    values[0, 0] = 0.3
    fine, _ = smooth_hazard_grid(values, (100.0, -0.5, 100.5, 0.0), 0.1, max_cells=50)
    assert np.isfinite(fine[0, 0]) and fine[0, 0] == pytest.approx(0.3)
    assert np.isnan(fine[-1, -1])  # four cells away from the only site


def test_nearest_site_returns_index_and_distance():
    frame = pd.DataFrame({"lon": [106.8, 110.0], "lat": [-6.2, -7.0]})
    index, distance = nearest_site(frame, 106.81, -6.2)
    assert index == 0
    assert distance == pytest.approx(1.106, abs=0.01)


def test_hazard_image_keeps_empty_cells_transparent():
    classes = np.array([[0, -1], [1, 1]])
    url = hazard_image_data_url(classes, (106.75, -6.35, 106.95, -6.15),
                                ("#ff0000", "#0000ff"), pixels_per_cell=2)
    image = Image.open(BytesIO(base64.b64decode(url.split(",", 1)[1]))).convert("RGBA")
    assert image.size == (4, 4)
    assert image.getpixel((0, 0))[:3] == (255, 0, 0)
    assert image.getpixel((3, 0))[3] == 0
    assert image.getpixel((3, 3))[:3] == (0, 0, 255)


def test_model_label_names_scientific_basis():
    from utils.psha_results import model_label

    assert model_label({"model": {"model_version": "v2024.0.0"}}) == (
        "PuSGeN 2024 via GEM IDN v2024.0.0")
    assert model_label({"model": {"model_version": "v2017.1.0"}}) == (
        "PuSGeN 2017 (Irsyam dkk.) via GEM IDN v2017.1.0")


def test_publication_model_label_leaves_out_the_project_name():
    from utils.psha_results import publication_model_label

    own = {"model": {"model_version": "v2024.0.0"},
           "source_model": {"name": "ntt_revisi1_sumber"}}
    label = publication_model_label(own)
    assert "ntt_revisi1_sumber" not in label
    assert label == "katalog Mw + PuSGeN 2024 (GEM IDN v2024.0.0)"
    assert publication_model_label({"model": {"model_version": "v2024.0.0"}}) == (
        "PuSGeN 2024 (GEM IDN v2024.0.0)")


def test_hazard_column_matches_imt_and_return_period(tmp_path):
    from utils.psha_results import hazard_column

    write_run(tmp_path, "dua_imt", imts=["PGA", "SA(0.2)"])
    run = list_psha_runs(tmp_path)[0]
    # The same probability picks the column of the requested IMT, never another
    # IMT's column (a stale widget object once coloured PGA with SA limits).
    assert hazard_column(run, "SA(0.2)", 0.02).column == "SA(0.2)-0.02"
    assert hazard_column(run, "PGA", 0.1).return_period_years == 475
    with pytest.raises(PSHAResultError):
        hazard_column(run, "SA(1.0)", 0.02)
    with pytest.raises(PSHAResultError):
        hazard_column(run, "PGA", 0.05)
