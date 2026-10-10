from __future__ import annotations

import json

from streamlit.testing.v1 import AppTest

PRESET = "GEM v2024 (PuSGeN 2024)"


def _wizard_script():
    import os
    from pathlib import Path

    from utils import psha_project

    psha_project.PROJECTS_DIR = Path(os.environ["WIZ_PROJECTS"])
    from psha_wizard import render_psha_wizard

    render_psha_wizard(Path(os.environ["WIZ_RESULTS"]))


def _button(at: AppTest, key: str):
    return next(b for b in at.button if b.key == key)


def test_wizard_walks_through_confirmed_steps(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    monkeypatch.setenv("WIZ_PROJECTS", str(projects))
    monkeypatch.setenv("WIZ_RESULTS", str(tmp_path / "results"))
    at = AppTest.from_function(_wizard_script, default_timeout=120)
    at.run()
    assert not at.exception

    at.text_input(key="wizard_new_name").input("uji").run()
    _button(at, "wizard_new_button").click().run()
    assert (projects / "uji.json").is_file()
    assert any("Konfirmasi wilayah" in b.label for b in at.button)

    _button(at, "wizard_confirm_uji_region").click().run()
    _button(at, "wizard_confirm_uji_source").click().run()
    project = json.loads((projects / "uji.json").read_text("utf-8"))
    assert set(project["confirmed"]) == {"region", "source"}
    assert project["region"]["provinces"] == ["Nusa Tenggara Timur"]

    # Step 3 cannot be confirmed with an empty logic tree.
    assert _button(at, "wizard_confirm_uji_gmpe").disabled
    _button(at, f"wizard_preset_uji_{PRESET}").click().run()
    assert not at.exception
    assert not _button(at, "wizard_confirm_uji_gmpe").disabled
    _button(at, "wizard_confirm_uji_gmpe").click().run()

    project = json.loads((projects / "uji.json").read_text("utf-8"))
    assert "gmpe" in project["confirmed"]
    interface = project["gmpe_logic_tree"]["Subduction Interface"]
    assert [b["weight"] for b in interface] == [0.34, 0.33, 0.33]
    # The run button stays locked until the review checkbox is ticked.
    assert _button(at, "wizard_start_uji").disabled
