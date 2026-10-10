from __future__ import annotations

import pytest

from utils import psha_project as pp


def test_new_project_has_unique_id_and_round_trips(tmp_path):
    first = pp.new_project("NTT uji GMPE", tmp_path)
    second = pp.new_project("NTT uji GMPE", tmp_path)
    assert first["id"] == "ntt_uji_gmpe" and second["id"] == "ntt_uji_gmpe_2"
    loaded = pp.load_project(first["id"], tmp_path)
    assert loaded["name"] == "NTT uji GMPE"
    assert sorted(p["id"] for p in pp.list_projects(tmp_path)) == [
        "ntt_uji_gmpe", "ntt_uji_gmpe_2"]


def test_steps_must_be_confirmed_in_order(tmp_path):
    project = pp.new_project("a", tmp_path)
    assert pp.current_step(project) == "region"
    assert not pp.unlocked(project, "gmpe")
    with pytest.raises(pp.ProjectError):
        pp.confirm_step(project, "source")
    pp.confirm_step(project, "region")
    pp.confirm_step(project, "source")
    assert pp.current_step(project) == "gmpe"
    assert pp.unlocked(project, "gmpe") and not pp.unlocked(project, "review")


def test_withdraw_from_clears_later_steps_and_run(tmp_path):
    project = pp.new_project("a", tmp_path)
    for step in ("region", "source", "gmpe", "review"):
        pp.confirm_step(project, step)
    project["run_id"] = "a_run1"
    withdrawn = pp.withdraw_from(project, "source")
    assert withdrawn == ["source", "gmpe", "review"]
    assert pp.current_step(project) == "source"
    assert project["run_id"] is None


def test_load_project_rejects_broken_files(tmp_path):
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(pp.ProjectError):
        pp.load_project("bad", tmp_path)
    assert pp.list_projects(tmp_path) == []


def test_source_model_trts_of_downloaded_gem_models():
    assert pp.source_model_trts("v2024.0.0") == [
        "Active Shallow Crust", "Subduction Interface", "Subduction Intraslab"]
    assert pp.source_model_trts("v2017.1.0") == [
        "Active Shallow Crust", "Subduction Interface", "Subduction Inslab"]
    summary = pp.source_model_summary("v2024.0.0")
    assert any(row["file"].startswith("ssm/interface/") for row in summary)
