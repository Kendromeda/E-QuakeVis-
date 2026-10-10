"""Candidate GMPEs, presets and GMPE logic-tree files for the PSHA wizard.

The wizard lets the user choose, per tectonic region type (TRT) present in the
source model, which GMPEs enter the logic tree and with what weight. This
module holds the curated candidate list (OpenQuake names plus short
explanations), presets taken from the GEM IDN models, validation, and the
writer of the OpenQuake ``gmmLT.xml`` file.

Model notes are summaries of the original publications; ranges are the
magnitude/distance ranges of the data each model was fitted to, rounded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
TRELLIS_SCRIPT = ROOT / "scripts" / "gmpe_trellis.py"

_ASC = "Active Shallow Crust"
_SIF = "Subduction Interface"
_SSL = "Subduction Intraslab"

TRT_LABELS = {
    _ASC: "Kerak dangkal (sesar aktif, gempa < ±25 km)",
    _SIF: "Megathrust (bidang kontak lempeng / interface)",
    _SSL: "Intraslab (di dalam lempeng yang menunjam)",
    "Subduction Inslab": "Intraslab (di dalam lempeng yang menunjam)",
}
TRT_EXPLANATIONS = {
    _ASC: (
        "Gempa dangkal di kerak lempeng atas, misalnya sesar naik busur belakang Flores "
        "dan sesar-sesar darat. GMPE kerak dangkal umumnya dari basis data NGA-West2 "
        "(California, Jepang, Taiwan, Tiongkok, Mediterania)."
    ),
    _SIF: (
        "Gempa pada bidang kontak antara lempeng Indo-Australia dan Sunda/Banda, "
        "termasuk megathrust selatan Jawa–Nusa Tenggara. Magnitudonya bisa sangat besar "
        "(M 8–9) dan guncangannya merambat jauh."
    ),
    _SSL: (
        "Gempa di dalam lempeng yang menunjam, dari puluhan sampai ratusan km "
        "kedalaman (misalnya di bawah Laut Flores). Pada jarak yang sama, gempa "
        "intraslab biasanya menghasilkan guncangan lebih kuat daripada interface."
    ),
}
TRT_EXPLANATIONS["Subduction Inslab"] = TRT_EXPLANATIONS[_SSL]


@dataclass(frozen=True)
class GMPE:
    """A candidate GMPE; ``key`` is what goes into the OpenQuake logic tree."""

    key: str
    label: str
    trt: str
    data: str
    ranges: str
    notes: str = ""
    used_by: tuple[str, ...] = ()
    year: int = 0


CANDIDATES: tuple[GMPE, ...] = (
    # Active shallow crust ----------------------------------------------------
    GMPE("BooreEtAl2014", "Boore dkk. 2014 (BSSA14)", _ASC, "NGA-West2 (global, kerak aktif)",
         "M 3–8,5; Rjb ≤ 400 km",
         "Bentuk sederhana (hanya Rjb dan Vs30); banyak dipakai sebagai acuan.",
         ("GEM v2017", "GEM v2024"), 2014),
    GMPE("CampbellBozorgnia2014", "Campbell & Bozorgnia 2014 (CB14)", _ASC, "NGA-West2",
         "M 3,3–8,5; Rrup ≤ 300 km",
         "Memperhitungkan efek hanging wall dan kedalaman cekungan (z2.5).",
         ("GEM v2017", "GEM v2024"), 2014),
    GMPE("ChiouYoungs2014", "Chiou & Youngs 2014 (CY14)", _ASC, "NGA-West2",
         "M 3,5–8,5; Rrup ≤ 300 km",
         "Memperhitungkan hanging wall, kedalaman puncak rupture, dan z1.0.",
         ("GEM v2017", "GEM v2024"), 2014),
    GMPE("AbrahamsonEtAl2014", "Abrahamson dkk. 2014 (ASK14)", _ASC, "NGA-West2",
         "M 3–8,5; Rrup ≤ 300 km",
         "Anggota keempat NGA-West2; sering dipakai bersama tiga model di atas.",
         (), 2014),
    GMPE("CauzziEtAl2014", "Cauzzi dkk. 2014", _ASC,
         "Global, didominasi rekaman K-NET/KiK-net Jepang", "M 4,5–7,9; Rrup ≤ 150 km",
         "Model global non-NGA; menambah keragaman data di logic tree.",
         ("GEM v2017",), 2014),
    GMPE("ZhaoEtAl2006Asc", "Zhao dkk. 2006 (kerak)", _ASC, "Jepang (ditambah sebagian global)",
         "M 5–8,3; Rrup ≤ 300 km",
         "Dari kelompok model yang sama dengan Zhao 2006 subduksi.",
         ("GEM v2017",), 2006),
    # Subduction interface ----------------------------------------------------
    GMPE("AbrahamsonGulerce2020SInter", "Abrahamson & Gülerce 2020 (global)",
         _SIF, "NGA-Sub (global)", "M 5–9+; Rrup hingga ±1000 km",
         "Model NGA-Sub; versi global tanpa penyesuaian regional.", (), 2020),
    GMPE("AbrahamsonGulerce2020SInterCascadia", "Abrahamson & Gülerce 2020 (Cascadia)", _SIF,
         "NGA-Sub, penyesuaian regional Cascadia", "M 5–9+; Rrup hingga ±1000 km",
         "Varian yang dipakai GEM v2024. Penyesuaian Cascadia memprediksi guncangan "
         "jauh lebih rendah daripada versi global; periksa di trellis.",
         ("GEM v2024",), 2020),
    GMPE("AbrahamsonEtAl2015SInter", "Abrahamson dkk. 2016 (BC Hydro)", _SIF,
         "Global (Jepang, Taiwan, Amerika Selatan, Meksiko, dll.)", "M 6–9; Rrup ≤ 300 km",
         "Model subduksi global yang sangat banyak dipakai sebelum NGA-Sub.",
         ("GEM v2017",), 2016),
    GMPE("ParkerEtAl2020SInter", "Parker dkk. 2020 (global)", _SIF,
         "NGA-Sub (global)", "M 4,5–9,1; Rrup ≤ 1000 km",
         "Model NGA-Sub dengan bentuk fungsional sederhana.", ("GEM v2017",), 2020),
    GMPE("KuehnEtAl2020SInter", "Kuehn dkk. 2020 (global)", _SIF,
         "NGA-Sub (global)", "M 4,5–9,2; Rrup ≤ 1000 km",
         "Model NGA-Sub berbasis Bayes; versi global.", ("GEM v2017",), 2020),
    GMPE("KuehnEtAl2020SInterCascadia", "Kuehn dkk. 2020 (Cascadia)", _SIF,
         "NGA-Sub, penyesuaian regional Cascadia", "M 4,5–9,2; Rrup ≤ 1000 km",
         "Varian yang dipakai GEM v2024.", ("GEM v2024",), 2020),
    GMPE("ZhaoEtAl2006SInter", "Zhao dkk. 2006 (interface)", _SIF,
         "Jepang (ditambah sebagian global)", "M 5–8,3; Rrup ≤ 300 km",
         "Dipakai di hampir semua model nasional Indonesia (2010, 2017, 2024).",
         ("GEM v2017", "GEM v2024"), 2006),
    GMPE("AtkinsonBoore2003SInter", "Atkinson & Boore 2003 (interface)", _SIF,
         "Global (Cascadia, Jepang, Meksiko, Amerika Tengah)", "M 5–8,3; Rrup ≤ 300 km",
         "Model lama yang dipakai peta Indonesia 2010; jenuh pada magnitudo sangat besar.",
         (), 2003),
    GMPE("YoungsEtAl1997SInter", "Youngs dkk. 1997 (interface)", _SIF,
         "Global (Alaska, Chili, Peru, Jepang, Meksiko, Solomon)", "M 5–8,2; Rrup 10–500 km",
         "Model klasik; bentuk fungsional lama, cenderung tinggi pada jarak jauh.", (), 1997),
    # Subduction intraslab ----------------------------------------------------
    GMPE("AbrahamsonGulerce2020SSlab", "Abrahamson & Gülerce 2020 (global)",
         _SSL, "NGA-Sub (global)", "M 5–8; Rrup hingga ±1000 km",
         "Model NGA-Sub; versi global tanpa penyesuaian regional.", (), 2020),
    GMPE("AbrahamsonGulerce2020SSlabCascadia", "Abrahamson & Gülerce 2020 (Cascadia)", _SSL,
         "NGA-Sub, penyesuaian regional Cascadia", "M 5–8; Rrup hingga ±1000 km",
         "Varian yang dipakai GEM v2024.", ("GEM v2024",), 2020),
    GMPE("AbrahamsonEtAl2015SSlab", "Abrahamson dkk. 2016 (BC Hydro)", _SSL,
         "Global", "M 5–8; Rrup ≤ 300 km",
         "Model subduksi global yang sangat banyak dipakai sebelum NGA-Sub.",
         ("GEM v2017",), 2016),
    GMPE("ParkerEtAl2020SSlab", "Parker dkk. 2020 (global)", _SSL,
         "NGA-Sub (global)", "M 4,5–8; Rrup ≤ 1000 km",
         "Model NGA-Sub dengan bentuk fungsional sederhana.", ("GEM v2017",), 2020),
    GMPE("KuehnEtAl2020SSlab", "Kuehn dkk. 2020 (global)", _SSL,
         "NGA-Sub (global)", "M 4,5–8; Rrup ≤ 1000 km",
         "Model NGA-Sub berbasis Bayes; versi global.", ("GEM v2017",), 2020),
    GMPE("KuehnEtAl2020SSlabCascadia", "Kuehn dkk. 2020 (Cascadia)", _SSL,
         "NGA-Sub, penyesuaian regional Cascadia", "M 4,5–8; Rrup ≤ 1000 km",
         "Varian yang dipakai GEM v2024.", ("GEM v2024",), 2020),
    GMPE("ZhaoEtAl2006SSlab", "Zhao dkk. 2006 (intraslab)", _SSL,
         "Jepang (ditambah sebagian global)", "M 5–8,3; Rrup ≤ 300 km",
         "Dipakai di hampir semua model nasional Indonesia.",
         ("GEM v2017", "GEM v2024"), 2006),
    GMPE("AtkinsonBoore2003SSlab", "Atkinson & Boore 2003 (intraslab)", _SSL,
         "Global", "M 5–8,3; Rrup ≤ 300 km",
         "Model lama yang dipakai peta Indonesia 2010.", (), 2003),
    GMPE("YoungsEtAl1997SSlab", "Youngs dkk. 1997 (intraslab)", _SSL,
         "Global", "M 5–8,2; Rrup 10–500 km",
         "Model klasik; bentuk fungsional lama.", (), 1997),
)

# Presets: (key, weight) per TRT. GEM weights are copied from the gmmLT.xml
# files of the downloaded models (data/psha/gem_idn_*/in/gmmLT.xml).
PRESETS: dict[str, dict[str, list[tuple[str, float]]]] = {
    "GEM v2024 (PuSGeN 2024)": {
        _ASC: [("BooreEtAl2014", 0.34), ("CampbellBozorgnia2014", 0.33),
               ("ChiouYoungs2014", 0.33)],
        _SIF: [("AbrahamsonGulerce2020SInterCascadia", 0.34), ("ZhaoEtAl2006SInter", 0.33),
               ("KuehnEtAl2020SInterCascadia", 0.33)],
        _SSL: [("AbrahamsonGulerce2020SSlabCascadia", 0.34), ("ZhaoEtAl2006SSlab", 0.33),
               ("KuehnEtAl2020SSlabCascadia", 0.33)],
    },
    "GEM v2017": {
        _ASC: [("BooreEtAl2014", 0.2), ("CampbellBozorgnia2014", 0.2),
               ("ChiouYoungs2014", 0.2), ("CauzziEtAl2014", 0.2), ("ZhaoEtAl2006Asc", 0.2)],
        _SIF: [("AbrahamsonEtAl2015SInter", 0.25),
               ("ParkerEtAl2020SInter", 0.25),
               ("ZhaoEtAl2006SInter", 0.25), ("KuehnEtAl2020SInter", 0.25)],
        _SSL: [("AbrahamsonEtAl2015SSlab", 0.25), ("ParkerEtAl2020SSlab", 0.25),
               ("ZhaoEtAl2006SSlab", 0.25), ("KuehnEtAl2020SSlab", 0.25)],
    },
}
WEIGHT_TOLERANCE = 1e-3


def canonical_trt(trt: str) -> str:
    """GEM v2017 calls intraslab "Subduction Inslab"."""
    return _SSL if trt == "Subduction Inslab" else trt


def candidates(trt: str) -> list[GMPE]:
    return [g for g in CANDIDATES if g.trt == canonical_trt(trt)]


def by_key(key: str) -> GMPE | None:
    return next((g for g in CANDIDATES if g.key == key), None)


def preset(name: str, trts: Iterable[str]) -> dict[str, list[dict]]:
    """Logic tree ``{trt: [{"gmpe": key, "weight": w}]}`` for the given TRTs."""
    table = PRESETS[name]
    return {trt: [{"gmpe": key, "weight": weight}
                  for key, weight in table.get(canonical_trt(trt), [])] for trt in trts}


def equal_weights(keys: list[str]) -> list[dict]:
    """Weights that sum to exactly 1 with two decimals (remainder on the first)."""
    if not keys:
        return []
    share = math.floor(100 / len(keys)) / 100
    weights = [share] * len(keys)
    weights[0] = round(1 - share * (len(keys) - 1), 2)
    return [{"gmpe": key, "weight": weight} for key, weight in zip(keys, weights)]


@dataclass
class Validation:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _family(key: str) -> str:
    name = key.split("\n")[0].strip("[]")
    return name.replace("Cascadia", "")


def validate(logic_tree: dict[str, list[dict]], trts: Iterable[str]) -> Validation:
    """Errors block the run; warnings are shown for the user to weigh."""
    result = Validation()
    for trt in trts:
        branches = logic_tree.get(trt, [])
        label = TRT_LABELS.get(trt, trt)
        if not branches:
            result.errors.append(f"{label}: belum ada GMPE yang dipilih.")
            continue
        total = sum(float(b["weight"]) for b in branches)
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            result.errors.append(
                f"{label}: jumlah bobot {total:.2f}, harus 1,00.".replace(".", ",", 1))
        if any(float(b["weight"]) <= 0 for b in branches):
            result.errors.append(f"{label}: setiap GMPE terpilih harus berbobot > 0.")
        keys = [b["gmpe"] for b in branches]
        if len(set(keys)) != len(keys):
            result.errors.append(f"{label}: ada GMPE yang dipilih dua kali.")
        unknown = [k for k in keys if by_key(k) is None]
        if unknown:
            result.errors.append(f"{label}: GMPE tidak dikenal: {', '.join(unknown)}.")
        wrong_trt = [by_key(k).label for k in keys
                     if by_key(k) is not None and by_key(k).trt != canonical_trt(trt)]
        if wrong_trt:
            result.errors.append(
                f"{label}: GMPE untuk tatanan tektonik lain: {', '.join(wrong_trt)}.")
        if len(branches) == 1:
            result.warnings.append(
                f"{label}: hanya satu GMPE, sehingga ketidakpastian model guncangan tanah "
                "tidak terwakili. Umumnya dipakai 3–5 GMPE.")
        families = [_family(k) for k in keys]
        if len(set(families)) < len(families):
            result.warnings.append(
                f"{label}: dua varian dari model yang sama dipilih bersamaan; keduanya "
                "sangat berkorelasi sehingga keragaman logic tree hampir tidak bertambah.")
        if len(branches) > 1 and any(float(b["weight"]) > 0.6 for b in branches):
            result.warnings.append(f"{label}: satu GMPE berbobot > 0,6 mendominasi hasil.")
    return result


def realizations(logic_tree: dict[str, list[dict]], trts: Iterable[str]) -> int:
    """Number of GMPE logic-tree paths (product of branches per TRT)."""
    count = 1
    for trt in trts:
        count *= max(1, len(logic_tree.get(trt, [])))
    return count


def gmpe_count(logic_tree: dict[str, list[dict]]) -> int:
    return sum(len(branches) for branches in logic_tree.values())


def write_gmm_logic_tree(logic_tree: dict[str, list[dict]], path: Path) -> None:
    """Write an OpenQuake GMPE logic tree (NRML 0.5)."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<nrml xmlns="http://openquake.org/xmlns/nrml/0.5">',
             '  <logicTree logicTreeID="equakevis_gmm">']
    for index, (trt, branches) in enumerate(logic_tree.items(), 1):
        lines.append(f'    <logicTreeBranchSet branchSetID="bs{index}" '
                     f'uncertaintyType="gmpeModel" '
                     f'applyToTectonicRegionType="{escape(trt)}">')
        for number, branch in enumerate(branches, 1):
            lines += [f'      <logicTreeBranch branchID="b{index}_{number}">',
                      f'        <uncertaintyModel>{escape(branch["gmpe"], quote=False)}'
                      '</uncertaintyModel>',
                      f'        <uncertaintyWeight>{float(branch["weight"]):.4f}'
                      '</uncertaintyWeight>',
                      '      </logicTreeBranch>']
        lines.append('    </logicTreeBranchSet>')
    lines += ['  </logicTree>', '</nrml>', '']
    path.write_text("\n".join(lines), encoding="utf-8")


def run_trellis(python: Path, request: dict, timeout: float = 300) -> dict:
    """Median/sigma curves from ``scripts/gmpe_trellis.py`` in the OpenQuake env."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    completed = subprocess.run(
        [str(python), str(TRELLIS_SCRIPT)], input=json.dumps(request), text=True,
        capture_output=True, timeout=timeout, cwd=ROOT, creationflags=flags)
    if completed.returncode != 0:
        lines = completed.stderr.strip().splitlines()
        raise RuntimeError(lines[-1] if lines else "Perhitungan trellis gagal.")
    return json.loads(completed.stdout)
