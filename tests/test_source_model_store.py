from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from utils import psha_jobs
from utils import source_model_store as sms
from utils.analysis_workbook import write_workbook
from utils.psha_jobs import PSHAJobError, start_job


def make_model(root: Path, name: str = "p_sumber") -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "ssmLT.xml").write_text("<nrml/>", encoding="utf-8")
    (path / "manifest.json").write_text(json.dumps({
        "site_bounds": [120.0, -9.0, 121.0, -8.0], "buffer_km": 250.0,
        "settings": {"min_mw_rates": 4.8, "smoothing_km": 40.0,
                     "mmax_shallow_background": 6.5, "mmax_intraslab_background": 7.8,
                     "include_unreviewed": False}}), encoding="utf-8")
    pd.DataFrame({
        "source_id": ["bg_0_25", "bg_200_300", "crust", "interface_S1"],
        "tectonic_region": ["Active Shallow Crust", "Subduction Intraslab",
                            "Active Shallow Crust", "Subduction Interface"],
        "n_sources": [100, 0, 12, 3],
    }).to_csv(path / "source_model_summary.csv", index=False)
    return path


def test_bounds_from_sites_adds_half_a_grid_step():
    assert sms.bounds_from_sites([(120.0, -9.0), (121.0, -8.0)], 0.2) == (
        119.9, -9.1, 121.1, -7.9)


def test_model_name_is_a_safe_folder_name():
    assert sms.model_name("ntt uji/1") == "ntt_uji_1_sumber"


def test_build_command_passes_every_option():
    command = sms.build_command("m", (120.0, -9.0, 121.0, -8.0),
                                {"min_mw": 4.8, "include_unreviewed": True}, Path("py"))
    assert command[command.index("--bounds") + 1:command.index("--bounds") + 5] == [
        "120", "-9", "121", "-8"]
    assert command[command.index("--min-mw") + 1] == "4.8"
    assert command[command.index("--buffer-km") + 1] == "300"
    assert command[-1] == "--include-unreviewed"
    assert "--start-year" not in command and "--end-year" not in command


def test_build_command_passes_the_year_limits():
    command = sms.build_command("m", (120.0, -9.0, 121.0, -8.0),
                                {"start_year": 1964, "end_year": 2020.0}, Path("py"))
    assert command[command.index("--start-year") + 1] == "1964"
    assert command[command.index("--end-year") + 1] == "2020"


def test_options_compare_equal_to_the_saved_choice(tmp_path):
    path = make_model(tmp_path)
    model = sms.load_source_model("p_sumber", tmp_path)
    # Models built before the year limits existed used every year.
    assert model.options["start_year"] is None and model.options["end_year"] is None
    chosen = {"buffer_km": 250, "min_mw": 4.8, "smoothing_km": 40, "mmax_shallow": 6.5,
              "mmax_slab": 7.8, "include_unreviewed": 0, "start_year": None, "end_year": None}
    assert model.options == sms.normalize_options(chosen)
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    manifest["settings"].update(start_year_limit=1964, end_year_limit=2020)
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    model = sms.load_source_model("p_sumber", tmp_path)
    assert model.options == sms.normalize_options({**chosen, "start_year": 1964,
                                                   "end_year": 2020})


def test_load_source_model_reads_options_and_tectonic_regions(tmp_path):
    make_model(tmp_path)
    model = sms.load_source_model("p_sumber", tmp_path)
    assert model.ready
    assert model.options["buffer_km"] == 250.0 and model.options["min_mw"] == 4.8
    # bg_200_300 has no sources, so Intraslab is not part of the model.
    assert model.tectonic_regions == ["Active Shallow Crust", "Subduction Interface"]
    assert sms.load_source_model("missing", tmp_path) is None


def test_build_source_model_requires_the_openquake_python(tmp_path):
    with pytest.raises(sms.SourceModelError):
        sms.build_source_model("m", (120.0, -9.0, 121.0, -8.0), {}, tmp_path / "none.exe",
                               tmp_path)


def test_write_workbook_puts_each_table_on_its_own_sheet(tmp_path):
    path = tmp_path / "a.xlsx"
    write_workbook(path, {"01 Satu": pd.DataFrame({"a": [1, 2]}),
                          "02 Dua": pd.DataFrame({"b": ["x"]})})
    sheets = pd.read_excel(path, sheet_name=None)
    assert list(sheets) == ["01 Satu", "02 Dua"]
    assert list(sheets["01 Satu"]["a"]) == [1, 2]


def test_start_job_passes_box_and_source_model(tmp_path, monkeypatch):
    python = tmp_path / "python.exe"
    python.write_text("", encoding="utf-8")
    model = make_model(tmp_path / "models")
    seen = {}
    monkeypatch.setattr(psha_jobs, "_spawn",
                        lambda command, env, log_path: seen.update(command=command) or 1)
    job = start_job("r", [], 0.1, 5, tmp_path / "results", python=python, oq_datadir=None,
                    bounds=(120.0, -9.0, 121.0, -8.0), source_model=model)
    command = seen["command"]
    assert command[command.index("--bounds") + 1:command.index("--bounds") + 5] == [
        "120", "-9", "121", "-8"]
    assert command[command.index("--source-model") + 1] == str(model)
    assert job.info["source_model"] == str(model)
    with pytest.raises(PSHAJobError):
        start_job("r2", [], 0.1, 5, tmp_path / "results", python=python, oq_datadir=None,
                  source_model=tmp_path / "not_a_model")
