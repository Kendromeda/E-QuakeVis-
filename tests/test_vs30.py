from __future__ import annotations

import numpy as np
import pytest

from utils import psha_jobs
from utils.psha_jobs import start_job
from utils.psha_results import vs30_label
from utils.psha_sites import build_sites
from utils.vs30 import Vs30Error, class_summary, load_grid, sample, sni_class


def grid():
    # 3 x 4 cells of 0.5 degrees from (100.0, -2.0); rows from south to north.
    return {"vs30": np.array([[200, 300, 400, 500], [600, 700, 800, 900],
                              [180, 250, 760, 1600]], dtype=np.int16),
            "lon0": 100.0, "lat0": -2.0, "step": 0.5}


def test_sample_takes_the_nearest_cell():
    values = sample([100.0, 100.6, 101.0, 101.5], [-2.0, -1.9, -1.5, -1.0], grid())
    assert list(values) == [200.0, 300.0, 800.0, 1600.0]
    with pytest.raises(Vs30Error):
        sample([110.0], [-2.0], grid())


def test_load_grid_reports_a_missing_file(tmp_path):
    with pytest.raises(Vs30Error):
        load_grid(tmp_path / "none.npz")
    path = tmp_path / "g.npz"
    np.savez_compressed(path, **grid())
    assert load_grid(path)["vs30"].shape == (3, 4)


def test_sni_classes_follow_the_vs30_limits():
    labels = sni_class([170, 175, 349, 350, 749, 750, 1500])
    assert [label.split()[0] for label in labels] == ["SE", "SD", "SD", "SC", "SC", "SB", "SA"]
    summary = class_summary([200, 300, 800]).set_index("Kelas situs SNI")
    assert summary.loc["SD (tanah sedang)", "Jumlah situs"] == 2


def test_vs30_label_describes_reference_and_usgs_runs():
    assert vs30_label({"vs30_m_s": 760.0}) == "Vs30 760 m/s"
    text = vs30_label({"vs30_m_s": 412.0, "vs30_source": "usgs", "vs30_min_m_s": 180.0,
                       "vs30_max_m_s": 900.0})
    assert "USGS" in text and "180" in text and "412" in text


def test_build_sites_limits_the_grid_to_a_box():
    square = {"type": "Feature", "properties": {"PROVINSI": "A"},
              "geometry": {"type": "Polygon",
                           "coordinates": [[[100, -2], [102, -2], [102, 0], [100, 0],
                                            [100, -2]]]}}
    sites = build_sites([square], None, 0.5, bounds=(100.9, -1.1, 101.6, -0.4))
    assert sorted(sites) == [(101.0, -1.0), (101.0, -0.5), (101.5, -1.0), (101.5, -0.5)]
    assert build_sites([square], None, 0.5, bounds=(110.0, 5.0, 111.0, 6.0)) == []


def test_start_job_passes_the_vs30_source(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    seen = {}
    monkeypatch.setattr(psha_jobs, "_spawn",
                        lambda command, env, log_path: seen.update(command=command) or 1)
    start_job("r", ["X"], 0.01, 5, tmp_path / "results", python=python, oq_datadir=None,
              vs30_source="usgs")
    command = seen["command"]
    assert command[command.index("--vs30-source") + 1] == "usgs"
    assert 0.01 in psha_jobs.SPACING_OPTIONS
