from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from utils import psha_jobs
from utils.psha_jobs import (
    PSHAJobError,
    active_job,
    delete_job,
    estimate_runtime_seconds,
    latest_progress,
    list_jobs,
    make_run_id,
    start_job,
    waiting_for_memory,
)
from utils.psha_sites import build_sites, load_province_features, province_names

SQUARE = {
    "type": "Feature",
    "properties": {"PROVINSI": "Kotak"},
    "geometry": {"type": "Polygon",
                 "coordinates": [[[106.0, -7.0], [107.0, -7.0], [107.0, -6.0],
                                  [106.0, -6.0], [106.0, -7.0]]]},
}


def write_job(results: Path, run_id: str, pid: int, started: str = "2026-10-07T00:00:00+00:00",
              finished: bool = False) -> Path:
    run_dir = results / run_id
    run_dir.mkdir(parents=True)
    info = {"run_id": run_id, "pid": pid, "started_utc": started, "provinces": [],
            "spacing_degrees": 0.2, "num_sites": 10, "estimated_seconds": 120}
    (run_dir / psha_jobs.JOB_FILE).write_text(json.dumps(info), encoding="utf-8")
    if finished:
        (run_dir / "manifest.json").write_text("{}", encoding="utf-8")
    return run_dir


def test_build_sites_coarser_grid_has_fewer_sites():
    fine = build_sites([SQUARE], ["Kotak"], 0.1)
    coarse = build_sites([SQUARE], ["Kotak"], 0.5)
    assert len(coarse) < len(fine)
    assert all(106.0 <= lon <= 107.0 and -7.0 <= lat <= -6.0 for lon, lat in coarse)


def test_build_sites_rejects_unknown_province():
    with pytest.raises(ValueError, match="tidak dikenal"):
        build_sites([SQUARE], ["Atlantis"], 0.1)


def test_build_sites_matches_west_java_run():
    features = load_province_features()
    sites = build_sites(features, ["Jawa Barat", "Banten", "DKI Jakarta"], 0.1)
    assert len(sites) == 382
    assert "Jawa Tengah" in province_names(features)


def test_make_run_id_is_readable_and_unused(tmp_path):
    names = ["Banten", "Jawa Barat", "Jawa Tengah", "DI Yogyakarta"]
    assert make_run_id(["Jawa Tengah", "DI Yogyakarta"], 0.2, names, tmp_path) == (
        "jawa_tengah-di_yogyakarta_0p2_v2024")
    assert make_run_id(names, 0.25, names, tmp_path, "v2017.1.0") == "indonesia_0p25_v2017"
    assert make_run_id([], 0.5, names, tmp_path) == "indonesia_0p5_v2024"
    (tmp_path / "banten_0p1_v2024").mkdir()
    assert make_run_id(["Banten"], 0.1, names, tmp_path) == "banten_0p1_v2024_2"


def test_estimate_scales_with_sites():
    assert estimate_runtime_seconds(382) == pytest.approx(20.6 * 60)
    assert estimate_runtime_seconds(100) < estimate_runtime_seconds(400)


def test_list_jobs_states(tmp_path):
    write_job(tmp_path, "running", os.getpid(), started="2026-10-07T02:00:00+00:00")
    write_job(tmp_path, "dead", 0, started="2026-10-07T01:00:00+00:00")
    write_job(tmp_path, "done", 0, finished=True)
    (tmp_path / "cli_run").mkdir()  # run_psha.py launched outside the app

    states = {job.run_id: job.state for job in list_jobs(tmp_path)}
    assert states == {"running": "running", "dead": "failed", "done": "finished"}
    assert active_job(tmp_path).run_id == "running"


def test_start_job_refuses_second_run(tmp_path):
    write_job(tmp_path, "running", os.getpid())
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    with pytest.raises(PSHAJobError, match="Masih ada"):
        start_job("baru", ["Banten"], 0.2, 10, tmp_path, python=python)


def test_start_job_launches_script_and_records_job(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    results = tmp_path / "results"
    launched = {}

    def fake_spawn(command, env, log_path):
        launched.update(command=command, env=env, log_path=log_path)
        return 4321

    monkeypatch.setattr(psha_jobs, "_spawn", fake_spawn)
    job = start_job("banten_0p2", ["Banten"], 0.2, 25, results, python=python,
                    oq_datadir=None)

    assert job.state == "running"
    assert launched["command"][2:] == [
        "--run-id", "banten_0p2", "--model", "v2024.0.0", "--spacing", "0.2",
        "--provinces", "Banten"]
    assert launched["env"]["PYTHONUNBUFFERED"] == "1"
    assert launched["log_path"] == results / "banten_0p2" / psha_jobs.LOG_FILE
    info = json.loads((results / "banten_0p2" / psha_jobs.JOB_FILE).read_text("utf-8"))
    assert info["pid"] == 4321 and info["num_sites"] == 25
    assert info["model_version"] == "v2024.0.0"
    with pytest.raises(PSHAJobError, match="sudah ada"):
        start_job("banten_0p2", ["Banten"], 0.2, 25, results, python=python)


def test_start_job_rejects_unknown_model(tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    with pytest.raises(PSHAJobError, match="tidak dikenal"):
        start_job("x", [], 0.5, 1, tmp_path / "results", python=python,
                  model_version="v1999")


def test_start_job_requires_openquake_environment(tmp_path):
    with pytest.raises(PSHAJobError, match="OpenQuake"):
        start_job("x", [], 0.5, 1, tmp_path, python=tmp_path / "missing.exe")


def test_latest_progress_reads_openquake_log(tmp_path):
    run_dir = write_job(tmp_path, "r", 0)
    (run_dir / psha_jobs.LOG_FILE).write_text(
        "[2026-10-06 06:34:10 #4 INFO] read_source_model 100% [8 submitted, 0 queued]\n"
        "[2026-10-06 06:35:13 #4 INFO] preclassical   9% [33 submitted, 0 queued]\n",
        encoding="utf-8")
    job = list_jobs(tmp_path)[0]
    assert latest_progress(job) == ("preclassical", 9)


def test_delete_job_only_removes_failed_runs(tmp_path):
    write_job(tmp_path, "done", 0, finished=True)
    write_job(tmp_path, "dead", 0)
    jobs = {job.run_id: job for job in list_jobs(tmp_path)}
    with pytest.raises(PSHAJobError):
        delete_job(jobs["done"], tmp_path)
    delete_job(jobs["dead"], tmp_path)
    assert not (tmp_path / "dead").exists()
    assert (tmp_path / "done").exists()


def test_waiting_for_memory_only_when_last_line_says_so(tmp_path):
    run_dir = write_job(tmp_path, "r", 0)
    log = run_dir / psha_jobs.LOG_FILE
    log.write_text("5 sites\n[2026-10-08 00:00:06 #7 INFO] Memory occupation 92%, "
                   "the user should free some memory\n", encoding="utf-8")
    job = list_jobs(tmp_path)[0]
    assert waiting_for_memory(job) == 92
    with log.open("a", encoding="utf-8") as handle:
        handle.write("[2026-10-08 00:05:00 #7 INFO] read_source_model  12% [8 submitted]\n")
    assert waiting_for_memory(job) is None


def test_start_job_passes_user_gmpe_logic_tree(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    results = tmp_path / "results"
    launched = {}
    monkeypatch.setattr(psha_jobs, "_spawn",
                        lambda command, env, log_path: launched.update(command=command) or 7)
    tree = {"Subduction Interface": [{"gmpe": "ZhaoEtAl2006SInter", "weight": 1.0}]}
    job = start_job("ntt_run1", ["Nusa Tenggara Timur"], 0.2, 102, results, python=python,
                    oq_datadir=None, gmm_logic_tree=tree, project_id="ntt")

    lt_file = results / "ntt_run1" / psha_jobs.GMM_LT_FILE
    assert json.loads(lt_file.read_text("utf-8")) == tree
    assert launched["command"][-2:] == ["--gmm-lt-json", str(lt_file)]
    assert job.info["project_id"] == "ntt"
    # One GMPE instead of the 13-GMPE baseline: a much shorter estimate.
    assert job.info["estimated_seconds"] < estimate_runtime_seconds(102)
