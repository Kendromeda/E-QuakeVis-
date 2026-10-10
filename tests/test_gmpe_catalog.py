from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from utils import gmpe_catalog as gc

TRTS = ["Active Shallow Crust", "Subduction Interface", "Subduction Intraslab"]


def test_every_trt_has_several_candidates_with_unique_keys():
    keys = [g.key for g in gc.CANDIDATES]
    assert len(keys) == len(set(keys))
    for trt in TRTS:
        assert len(gc.candidates(trt)) >= 5
    # GEM v2017 names intraslab "Subduction Inslab".
    assert gc.candidates("Subduction Inslab") == gc.candidates("Subduction Intraslab")


@pytest.mark.parametrize("name", list(gc.PRESETS))
def test_presets_are_valid_logic_trees(name):
    tree = gc.preset(name, TRTS)
    check = gc.validate(tree, TRTS)
    assert check.ok, check.errors
    assert all(gc.by_key(b["gmpe"]) is not None for branches in tree.values()
               for b in branches)


def test_gem_v2024_preset_matches_downloaded_model():
    tree = gc.preset("GEM v2024 (PuSGeN 2024)", TRTS)
    assert gc.realizations(tree, TRTS) == 27
    assert [b["weight"] for b in tree["Subduction Interface"]] == [0.34, 0.33, 0.33]


def test_validate_reports_errors_and_warnings():
    tree = {
        "Active Shallow Crust": [{"gmpe": "BooreEtAl2014", "weight": 0.5}],
        "Subduction Interface": [{"gmpe": "ZhaoEtAl2006SInter", "weight": 1.0}],
        "Subduction Intraslab": [{"gmpe": "ZhaoEtAl2006SInter", "weight": 1.0}],
    }
    check = gc.validate(tree, TRTS)
    assert not check.ok
    joined = " ".join(check.errors)
    assert "harus 1,00" in joined
    assert "tatanan tektonik lain" in joined
    assert any("hanya satu GMPE" in w for w in check.warnings)
    assert "belum ada GMPE" in " ".join(gc.validate({}, TRTS).errors)


def test_validate_warns_on_two_variants_of_one_model():
    tree = {"Subduction Interface": [
        {"gmpe": "KuehnEtAl2020SInter", "weight": 0.5},
        {"gmpe": "KuehnEtAl2020SInterCascadia", "weight": 0.5}]}
    check = gc.validate(tree, ["Subduction Interface"])
    assert check.ok
    assert any("varian" in w for w in check.warnings)


def test_equal_weights_sum_to_one():
    for n in range(1, 8):
        weights = [b["weight"] for b in gc.equal_weights([f"g{i}" for i in range(n)])]
        assert sum(weights) == pytest.approx(1.0)
    assert gc.equal_weights([]) == []


def test_write_gmm_logic_tree_is_nrml(tmp_path):
    tree = gc.preset("GEM v2024 (PuSGeN 2024)", TRTS)
    path = tmp_path / "gmmLT.xml"
    gc.write_gmm_logic_tree(tree, path)
    root = ET.parse(path).getroot()
    ns = {"n": "http://openquake.org/xmlns/nrml/0.5"}
    branch_sets = root.findall(".//n:logicTreeBranchSet", ns)
    assert [bs.get("applyToTectonicRegionType") for bs in branch_sets] == TRTS
    models = [m.text for m in root.findall(".//n:uncertaintyModel", ns)]
    assert "AbrahamsonGulerce2020SInterCascadia" in models
    weights = [float(w.text) for w in branch_sets[0].findall(".//n:uncertaintyWeight", ns)]
    assert sum(weights) == pytest.approx(1.0)
