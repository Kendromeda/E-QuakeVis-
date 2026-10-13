"""Tab "Bangun Model PSHA": a step-by-step PSHA wizard.

Every step explains what is being decided, shows the consequences (site
count, source-model contents, GMPE trellis, validation, runtime) and must be
confirmed before the next step opens. Changing a confirmed step withdraws the
confirmation of later steps. The logic lives in ``utils.psha_project``,
``utils.gmpe_catalog`` and ``utils.psha_jobs``; this module is the UI only.
"""
from __future__ import annotations

from html import escape as escape_html
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.errors import StreamlitAPIException

from utils import gmpe_catalog as gc
from utils import psha_project as pp
from utils import source_model_store as sms
from utils import vs30
from utils.bbox_selection import format_bounds
from utils.psha_jobs import (
    MAX_SITES,
    OQ_PYTHON,
    SPACING_OPTIONS,
    PSHAJobError,
    active_job,
    cancel_job,
    estimate_runtime_seconds,
    format_duration,
    latest_progress,
    list_jobs,
    log_tail,
    memory_percent,
    oq_available,
    OQ_MEMORY_LIMIT_PERCENT,
    start_job,
    waiting_for_memory,
)
from utils.psha_results import (
    IMT_LABELS,
    PSHAResultError,
    list_psha_runs,
    load_hazard_map,
    model_label,
)
from utils.psha_sites import (
    KABUPATEN_KEY,
    build_sites,
    kabupaten_names,
    load_kabupaten_features,
    load_province_features,
    province_names,
)

DEFAULT_PROVINCES = ["Nusa Tenggara Timur"]
SOURCE_OPTIONS = {
    "v2024.0.0": "Tahap A – Model sumber GEM IDN v2024 (PuSGeN 2024)",
    "v2017.1.0": "Tahap A – Model sumber GEM IDN v2017 (PuSGeN 2017)",
    "own": "Tahap B – Model sumber sendiri dari katalog (gempa latar) + sesar/megathrust "
           "PuSGeN 2024",
}
# Tahap B keeps GEM v2024's rupture settings, GMPE choices and reqv tables.
OWN_BASE_VERSION = "v2024.0.0"
TRELLIS_MAGS = {
    "Active Shallow Crust": (5.5, 6.5, 7.5),
    "Subduction Interface": (7.0, 8.0, 9.0),
    "Subduction Intraslab": (6.0, 7.0, 7.5),
    "Subduction Inslab": (6.0, 7.0, 7.5),
}
TRELLIS_DISTANCES = [round(float(d), 1) for d in np.geomspace(5, 400, 30)]
STEP_ICONS = {"done": "✅", "current": "▶️", "locked": "🔒"}


# --- Cached helpers ------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _province_names() -> list[str]:
    return province_names(load_province_features())


@st.cache_data(show_spinner=False)
def _kabupaten_names(province: str) -> list[str]:
    return kabupaten_names(load_kabupaten_features(), province)


@st.cache_data(show_spinner=False)
def _sites(provinces: tuple[str, ...], spacing: float,
           kabupaten: tuple[str, ...] = ()) -> list[tuple[float, float]]:
    if kabupaten:
        return build_sites(load_kabupaten_features(), kabupaten, spacing, key=KABUPATEN_KEY)
    return build_sites(load_province_features(), provinces, spacing)


@st.cache_resource(show_spinner=False)
def _vs30_grid() -> dict:
    return vs30.load_grid()


@st.cache_data(show_spinner=False)
def _usgs_vs30(sites: tuple[tuple[float, float], ...]) -> np.ndarray:
    return vs30.sample([s[0] for s in sites], [s[1] for s in sites], _vs30_grid())


def _vs30_text(region: dict[str, Any]) -> str:
    if region.get("vs30_source") == "usgs":
        return "Vs30 USGS per situs"
    return f"Vs30 {region.get('vs30', 760.0):g} m/s"


@st.cache_data(show_spinner=False)
def _box_sites(bounds: tuple[float, ...], spacing: float) -> list[tuple[float, float]]:
    """Land sites of every province that fall in the box."""
    return build_sites(load_province_features(), None, spacing, bounds=tuple(bounds))


def _region_sites(region: dict[str, Any]) -> list[tuple[float, float]]:
    if region.get("bounds") and not region.get("provinces") and not region.get("kabupaten"):
        return _box_sites(tuple(region["bounds"]), float(region["spacing"]))
    return _sites(tuple(region.get("provinces", [])), float(region["spacing"]),
                  tuple(region.get("kabupaten", [])))


def _region_label(region: dict[str, Any]) -> str:
    if region.get("kabupaten"):
        return "Kabupaten/kota " + ", ".join(region["kabupaten"])
    if region.get("bounds") and not region.get("provinces"):
        return "Kotak " + format_bounds(tuple(region["bounds"]))
    return ", ".join(region.get("provinces", []))


def _source_label(source: dict[str, Any]) -> str:
    if source.get("kind") == "own":
        return f"{SOURCE_OPTIONS['own']} — model `{source.get('name', '?')}`"
    return SOURCE_OPTIONS.get(source.get("version", ""), source.get("version", ""))


@st.cache_data(show_spinner=False)
def _source_summary(version: str) -> list[dict[str, Any]]:
    return pp.source_model_summary(version)


@st.cache_data(show_spinner=False)
def _source_trts(version: str) -> list[str]:
    return pp.source_model_trts(version)


@st.cache_data(show_spinner="Menghitung kurva GMPE (trellis)...")
def _trellis(request_json: str) -> dict:
    return gc.run_trellis(OQ_PYTHON, json.loads(request_json))


# --- Project handling ----------------------------------------------------------

def _rerun() -> None:
    """Rerun only the wizard when possible (a fragment rerun), else the app."""
    try:
        st.rerun(scope="fragment")
    except StreamlitAPIException:  # The wizard ran as part of a full app run.
        st.rerun()


def _save(project: dict[str, Any]) -> None:
    pp.save_project(project)


def _project_selector() -> dict[str, Any] | None:
    projects = pp.list_projects()
    with st.container(border=True):
        cols = st.columns([3, 2, 1])
        options = [p["id"] for p in projects]
        names = {p["id"]: p["name"] for p in projects}
        selected = None
        if options:
            default = st.session_state.get("wizard_project")
            index = options.index(default) if default in options else 0
            selected = cols[0].selectbox(
                "Proyek model PSHA", options, index=index,
                format_func=lambda pid: f"{names[pid]} ({pid})", key="wizard_project_select")
        else:
            cols[0].info("Belum ada proyek. Buat proyek pertama di sebelah kanan.")
        new_name = cols[1].text_input("Nama proyek baru", placeholder="misalnya NTT uji GMPE",
                                      key="wizard_new_name")
        cols[2].write("")
        if cols[2].button("Buat", key="wizard_new_button", disabled=not new_name.strip()):
            project = pp.new_project(new_name)
            st.session_state["wizard_project"] = project["id"]
            st.session_state.pop("wizard_project_select", None)
            _rerun()
    if selected is None:
        return None
    st.session_state["wizard_project"] = selected
    return pp.load_project(selected)


def _progress_bar(project: dict[str, Any]) -> None:
    current = pp.current_step(project)
    cells = []
    for step in pp.STEPS:
        if pp.is_confirmed(project, step):
            state = "done"
        elif step == current:
            state = "current"
        else:
            state = "locked"
        cells.append(f"{STEP_ICONS[state]} {pp.STEP_TITLES[step]}")
    st.markdown("  →  ".join(cells))


def _notes(project: dict[str, Any], step: str, prompt: str) -> str:
    return st.text_area(
        prompt, value=project.get("notes", {}).get(step, ""), height=80,
        key=f"wizard_note_{project['id']}_{step}",
        help="Catatan ini disimpan di proyek dan ditampilkan lagi di langkah Tinjau.")


def _confirm_button(project: dict[str, Any], step: str, label: str, *,
                    disabled: bool = False) -> bool:
    already = pp.is_confirmed(project, step)
    later = [s for s in pp.STEPS[pp.STEPS.index(step) + 1:] if pp.is_confirmed(project, s)]
    if already and later:
        st.caption("Menyimpan perubahan di langkah ini akan membatalkan konfirmasi langkah "
                   + ", ".join(pp.STEP_TITLES[s] for s in later) + ".")
    text = "Simpan perubahan" if already else label
    return st.button(text, type="primary", disabled=disabled,
                     key=f"wizard_confirm_{project['id']}_{step}")


def _commit(project: dict[str, Any], step: str) -> None:
    pp.withdraw_from(project, step)
    pp.confirm_step(project, step)
    _save(project)
    _rerun()


# --- Step 1: region ------------------------------------------------------------

def _step_region(project: dict[str, Any]) -> None:
    st.markdown(
        "**Apa yang diputuskan:** titik-titik (situs) tempat bahaya gempa dihitung, dan "
        "kondisi tanah di situs itu.\n\n"
        "- **Situs** adalah titik grid di daratan provinsi atau kabupaten/kota terpilih. "
        "Sumber gempa di luar wilayah itu tetap diperhitungkan (sampai 300 km, megathrust "
        "sampai 1000 km); wilayah kecil hanya mengurangi jumlah titik yang dihitung.\n"
        "- **Jarak grid** menentukan kerapatan titik. Makin rapat makin detail, tetapi "
        "waktu hitung naik sebanding jumlah situs.\n"
        "- **Kondisi situs**: bawaannya batuan referensi Vs30 = 760 m/s (batas kelas situs "
        "SB, SNI 1726:2019), sama dengan peta gempa nasional; efek tanah setempat ditambahkan "
        "kemudian lewat koefisien situs. Pilihan lain: Vs30 USGS per situs (bahaya di "
        "permukaan).\n"
        "- **Grid 0,01° (±1 km)** hanya untuk wilayah kecil (kabupaten/kota atau kotak); "
        "batasnya 10.000 situs.\n"
        "- **Periode ulang**: 475 tahun (10% dalam 50 tahun) dan 2.475 tahun (2% dalam "
        "50 tahun), untuk PGA, SA 0,2 detik, dan SA 1,0 detik."
    )
    region = project["region"]
    levels = {"provinsi": "Provinsi", "kabupaten": "Kabupaten/kota",
              "kotak": "Kotak dari filter peta"}
    saved_level = ("kabupaten" if region.get("kabupaten")
                   else "kotak" if region.get("bounds") and not region.get("provinces")
                   else "provinsi")
    level = st.radio("Tingkat wilayah", list(levels), format_func=levels.get, horizontal=True,
                     index=list(levels).index(saved_level),
                     key=f"wizard_level_{project['id']}")
    kabupaten: list[str] = []
    bounds = None
    if level == "kotak":
        selected = st.session_state.get("selection_bbox")
        bounds = tuple(selected) if selected else (
            tuple(region["bounds"]) if region.get("bounds") else None)
        if bounds is None:
            st.info("Belum ada kotak. Gambar kotak di peta sebaran, atau isi koordinat "
                    "North/West/East/South di sidebar **Filter**, lalu kembali ke sini.")
            return
        st.markdown(f"Kotak yang dipakai: **{format_bounds(bounds)}**"
                    + ("" if selected else " (tersimpan di proyek; filter peta kosong)"))
        st.caption("Situs adalah titik daratan di dalam kotak. Untuk Tahap B, gempa diambil "
                   "dari kotak ini ditambah buffer (langkah 2).")
        provinces = []
    elif level == "kabupaten":
        names = _province_names()
        current = (region.get("provinces") or DEFAULT_PROVINCES)[0]
        province = st.selectbox("Provinsi", names,
                                index=names.index(current) if current in names else 0,
                                key=f"wizard_kab_province_{project['id']}")
        provinces = [province]
        options = _kabupaten_names(province)
        kabupaten = st.multiselect(
            "Kabupaten/kota", options,
            default=[k for k in region.get("kabupaten", []) if k in options],
            key=f"wizard_kabupaten_{project['id']}")
    else:
        provinces = st.multiselect(
            "Provinsi", _province_names(),
            default=region.get("provinces") or DEFAULT_PROVINCES,
            key=f"wizard_provinces_{project['id']}")
    spacing_now = region.get("spacing", 0.2)
    spacing = st.radio(
        "Jarak grid situs", SPACING_OPTIONS,
        index=SPACING_OPTIONS.index(spacing_now if spacing_now in SPACING_OPTIONS else 0.2),
        format_func=lambda v: f"{v:g}° (±{v * 111:.1f} km)".replace(".", ","),
        horizontal=True, key=f"wizard_spacing_{project['id']}")
    if level != "kotak" and (not provinces or (level == "kabupaten" and not kabupaten)):
        st.warning("Pilih minimal satu wilayah.")
        return
    if level == "kotak":
        sites = _box_sites(tuple(bounds), float(spacing))
    else:
        sites = _sites(tuple(provinces), float(spacing), tuple(kabupaten))
    cols = st.columns(3)
    cols[0].metric("Jumlah situs", f"{len(sites):,}".replace(",", "."))
    cols[1].metric("Jarak antar situs", f"±{float(spacing) * 111:.1f} km".replace(".", ","))
    cols[2].metric("Perkiraan waktu (13 GMPE)", format_duration(
        estimate_runtime_seconds(len(sites))))
    conditions = {"reference": "Batuan referensi Vs30 760 m/s (bawaan, setara peta nasional)",
                  "usgs": "Vs30 USGS per situs (proxy kemiringan topografi, Heath dkk. 2020)"}
    vs30_source = st.radio("Kondisi situs", list(conditions), format_func=conditions.get,
                           index=1 if region.get("vs30_source") == "usgs" else 0,
                           key=f"wizard_vs30_{project['id']}")
    if vs30_source == "usgs" and sites:
        try:
            values = _usgs_vs30(tuple(sites))
        except vs30.Vs30Error as exc:
            st.error(str(exc))
            return
        st.caption(
            f"Vs30 di {len(sites)} situs: {values.min():.0f}–{values.max():.0f} m/s, median "
            f"{np.median(values):.0f} m/s. Hasilnya bahaya di **permukaan** (termasuk "
            "amplifikasi tanah dari GMPE), jadi tidak bisa dibandingkan langsung dengan peta "
            "SNI di batuan. Nilai USGS adalah proxy kasar dari kemiringan topografi, bukan "
            "pengukuran; untuk kajian lokal gunakan data mikrotremor/MASW/bor.")
        st.dataframe(vs30.class_summary(values), hide_index=True, use_container_width=True)
    too_many = len(sites) > MAX_SITES
    if too_many:
        st.error(f"{len(sites):,} situs terlalu banyak (batas {MAX_SITES:,}, sekitar sehari "
                 "hitung di laptop ini). Grid 1 km cocok untuk satu kabupaten/kota atau kotak "
                 "kecil; untuk provinsi pakai 0,05° atau lebih.".replace(",", "."))
    if sites:
        st.map(pd.DataFrame(sites[:20000], columns=["lon", "lat"]), latitude="lat",
               longitude="lon", size=max(150, int(spacing * 15000)), height=320)
    note = _notes(project, "region", "Catatan: mengapa wilayah dan grid ini dipilih?")
    if _confirm_button(project, "region", "Konfirmasi wilayah",
                       disabled=not sites or too_many):
        project["region"] = {"provinces": provinces, "kabupaten": kabupaten,
                             "spacing": float(spacing), "vs30": 760.0,
                             "vs30_source": vs30_source,
                             **({"bounds": [float(v) for v in bounds]} if bounds else {})}
        project.setdefault("notes", {})["region"] = note
        _commit(project, "region")


# --- Step 2: source model --------------------------------------------------------

def _source_kind(path: str) -> str:
    if "interface" in path or "/sub/" in path:
        return "Megathrust (bidang sesar kompleks)"
    if "crust" in path:
        return "Sesar dangkal (bidang sesar)"
    return "Seismisitas latar (grid titik sumber)"


def _step_source(project: dict[str, Any]) -> None:
    st.markdown(
        "**Apa yang diputuskan:** model sumber gempa, yaitu *di mana*, *seberapa besar*, dan "
        "*seberapa sering* gempa terjadi. Model ini dibangun dari katalog gempa (yang sudah "
        "dideklasterisasi), data sesar (slip rate), dan geometri megathrust.\n\n"
        "**Tahap A** memakai model sumber yang sudah dibangun tim PuSGeN dan diimplementasikan "
        "GEM. **Tahap B** membangun gempa latar sendiri dari katalog Mw gabungan (ISC dan "
        "ISC-GEM) untuk wilayah langkah 1: deklasterisasi, kelengkapan, nilai a-b, lalu "
        "penghalusan; sesar dan megathrust tetap dari PuSGeN 2024 karena geometri dan laju "
        "slip-nya tidak bisa diturunkan dari katalog."
    )
    saved = project["source_model"]
    current = "own" if saved.get("kind") == "own" else saved.get("version", "v2024.0.0")
    options = list(SOURCE_OPTIONS)
    version = st.radio("Model sumber", options, format_func=SOURCE_OPTIONS.get,
                       index=options.index(current) if current in options else 0,
                       key=f"wizard_source_{project['id']}")
    if version == "own":
        _own_source_step(project)
        return
    try:
        summary = _source_summary(version)
        trts = _source_trts(version)
    except pp.ProjectError as exc:
        st.error(str(exc))
        return
    table = pd.DataFrame(summary)
    table["Jenis"] = table["file"].map(_source_kind)
    table["Tatanan tektonik"] = table["trt"].map(lambda t: gc.TRT_LABELS.get(t, t))
    table["Ukuran"] = (table["bytes"] / 1e6).map(lambda v: f"{v:.1f} MB".replace(".", ","))
    st.dataframe(table[["file", "Jenis", "Tatanan tektonik", "sources", "Ukuran"]].rename(
        columns={"file": "Berkas", "sources": "Elemen sumber"}), hide_index=True,
        use_container_width=True)
    if version == "v2024.0.0":
        st.caption("Megathrust v2024 memiliki dua skenario segmentasi (S1 bobot 0,9 dan S2 "
                   "bobot 0,1), masing-masing dengan MFD Gutenberg-Richter dan karakteristik. "
                   "Lisensi CC BY-NC-SA 4.0: hanya untuk penggunaan non-komersial.")
    st.markdown(
        "Model ini memuat **" + str(len(trts)) + " tatanan tektonik**: "
        + ", ".join(gc.TRT_LABELS.get(t, t) for t in trts)
        + ". Di langkah berikutnya Anda memilih GMPE untuk masing-masing.")
    note = _notes(project, "source", "Catatan: alasan memilih model sumber ini")
    if _confirm_button(project, "source", "Konfirmasi model sumber"):
        if project["source_model"].get("version") != version or saved.get("kind") == "own":
            project["gmpe_logic_tree"] = {}
            st.session_state.pop(f"wizard_lt_seeded_{project['id']}", None)
        project["source_model"] = {"kind": "gem", "version": version, "trts": trts}
        project.setdefault("notes", {})["source"] = note
        _commit(project, "source")


def _region_bounds(region: dict[str, Any]) -> tuple | None:
    if region.get("bounds"):
        return tuple(region["bounds"])
    sites = _region_sites(region)
    return sms.bounds_from_sites(sites, float(region["spacing"])) if sites else None


def _own_source_step(project: dict[str, Any]) -> None:
    """Tahap B: build the source model from the catalogue for the region's box."""
    bounds = _region_bounds(project["region"])
    if bounds is None:
        st.warning("Wilayah langkah 1 tidak punya situs.")
        return
    saved = project["source_model"] if project["source_model"].get("kind") == "own" else {}
    options = {**sms.DEFAULT_OPTIONS, **saved.get("options", {})}
    st.markdown(
        f"**Kotak situs:** {format_bounds(bounds)}. Gempa diambil dari kotak ini ditambah "
        "buffer, karena sumber di luar kotak tetap mengguncang situs di dalamnya.\n\n"
        "1. **Katalog Mw gabungan**: satu Mw per gempa (Mw langsung > proxy ISC-GEM > "
        "konversi skala lain).\n"
        "2. **Deklasterisasi** Gardner–Knopoff (1974): gempa susulan dan pendahulu dibuang "
        "karena PSHA mengasumsikan gempa utama saling bebas (proses Poisson).\n"
        "3. **Kelengkapan** Stepp (1972): sejak tahun berapa gempa tiap ukuran tercatat "
        "lengkap.\n"
        "4. **Nilai a-b** Weichert (1980) per lapisan kedalaman (seperti 6 lapisan PuSGeN).\n"
        "5. **Penghalusan** Frankel (1995): laju gempa disebar dengan kernel Gaussian.\n"
        "6. **Sesar dan megathrust PuSGeN 2024** yang dekat kotak ditambahkan.")
    pid = project["id"]
    cols = st.columns(3)
    buffer_km = cols[0].number_input("Buffer sumber (km)", 100.0, 600.0,
                                     float(options["buffer_km"]), 50.0, key=f"sm_buf_{pid}",
                                     help="Gempa dan sesar sejauh ini dari kotak ikut dihitung "
                                          "(megathrust sampai 1000 km).")
    min_mw = cols[1].number_input("Mw minimum untuk laju", 4.0, 5.5, float(options["min_mw"]),
                                  0.1, key=f"sm_mmin_{pid}",
                                  help="Konversi magnitudo hanya sah di atas ±Mw 4,5.")
    smoothing = cols[2].number_input("Jarak penghalusan (km)", 10.0, 150.0,
                                     float(options["smoothing_km"]), 5.0, key=f"sm_smooth_{pid}")
    cols = st.columns(3)
    mmax_shallow = cols[0].number_input("Mmax gempa latar dangkal", 5.5, 7.5,
                                        float(options["mmax_shallow"]), 0.1,
                                        key=f"sm_mmax_s_{pid}",
                                        help="Gempa lebih besar ditanggung sesar dan megathrust "
                                             "(PuSGeN: 6,5).")
    mmax_slab = cols[1].number_input("Mmax gempa latar intraslab", 6.5, 8.5,
                                     float(options["mmax_slab"]), 0.1, key=f"sm_mmax_d_{pid}")
    unreviewed = cols[2].checkbox("Sertakan gempa belum direview ISC (±24 bulan terakhir)",
                                  value=bool(options["include_unreviewed"]),
                                  key=f"sm_unrev_{pid}")
    chosen = {"buffer_km": buffer_km, "min_mw": min_mw, "smoothing_km": smoothing,
              "mmax_shallow": mmax_shallow, "mmax_slab": mmax_slab,
              "include_unreviewed": unreviewed}

    name = sms.model_name(pid)
    model = sms.load_source_model(name)
    stale = model is not None and (
        [round(v, 4) for v in model.manifest.get("site_bounds", [])]
        != [round(v, 4) for v in bounds])
    if st.button("Bangun ulang model sumber" if model else "Bangun model sumber",
                 key=f"sm_build_{pid}"):
        with st.spinner("Membangun model sumber (katalog, deklasterisasi, a-b, "
                        "penghalusan, sesar)..."):
            try:
                model = sms.build_source_model(name, bounds, chosen, OQ_PYTHON)
                stale = False
            except sms.SourceModelError as exc:
                st.error(str(exc))
                return
    if model is None:
        st.info("Model sumber belum dibangun untuk proyek ini.")
        return
    if stale:
        st.warning("Model sumber dibangun untuk kotak lain. Bangun ulang supaya sesuai "
                   "wilayah langkah 1.")
    elif model.options != {k: (bool(v) if k == "include_unreviewed" else float(v))
                           for k, v in chosen.items()}:
        st.warning("Pengaturan di atas berbeda dari model yang sudah dibangun. Tekan "
                   "**Bangun ulang model sumber** untuk memakainya; tabel di bawah masih "
                   "hasil pengaturan lama.")
    _own_source_report(model)
    note = _notes(project, "source", "Catatan: alasan memilih model sumber ini")
    if _confirm_button(project, "source", "Konfirmasi model sumber", disabled=stale):
        if saved.get("name") != name or project["source_model"].get("kind") != "own":
            project["gmpe_logic_tree"] = {}
            st.session_state.pop(f"wizard_lt_seeded_{pid}", None)
        project["source_model"] = {
            "kind": "own", "version": OWN_BASE_VERSION, "name": name,
            "path": str(model.path.relative_to(sms.ROOT)).replace("\\", "/"),
            "bounds": list(bounds), "options": model.options,
            "trts": model.tectonic_regions}
        project.setdefault("notes", {})["source"] = note
        _commit(project, "source")


def _own_source_report(model: sms.SourceModel) -> None:
    manifest = model.manifest
    counts = manifest.get("counts", {})
    sel, dec = counts.get("selection", {}), counts.get("declustering", {})
    for warning in manifest.get("warnings", []):
        st.warning(warning)
    cols = st.columns(4)
    cols[0].metric("Gempa terpilih", f"{sel.get('selected', 0):,}".replace(",", "."))
    cols[1].metric("Gempa utama", f"{dec.get('mainshocks', 0):,}".replace(",", "."))
    cols[2].metric("Dibuang (susulan/pendahulu)",
                   f"{dec.get('removed', 0):,}".replace(",", "."))
    cols[3].metric("Tahun akhir katalog", manifest.get("end_year", "-"))
    try:
        summary = model.table("source_model_summary.csv")
        ab = model.table("05_ab_values.csv")
        completeness = model.table("04_completeness.csv")
    except sms.SourceModelError as exc:
        st.error(str(exc))
        return
    st.markdown("**Ringkasan model sumber** (satu baris per sumber)")
    st.dataframe(summary[["source_id", "source_type", "tectonic_region", "depth_layer",
                          "n_sources", "b_value", "mmin", "mmax", "annual_rate_mw_ge_mmin",
                          "rate_mw_ge_5_per_year", "rate_mw_ge_7_per_year", "note"]],
                 hide_index=True, use_container_width=True)
    left, right = st.columns(2)
    left.markdown("**Nilai a-b per lapisan (Weichert)**")
    left.dataframe(ab[["depth_layer", "n_events", "n_used_complete", "b_value", "b_sigma",
                       "a_value", "mw_max_observed", "reliable"]],
                   hide_index=True, use_container_width=True)
    right.markdown("**Kelengkapan katalog (Stepp)**")
    right.dataframe(completeness, hide_index=True, use_container_width=True)
    workbook = model.path / sms.WORKBOOK
    cols = st.columns(2)
    if workbook.is_file():
        cols[0].download_button(
            "Unduh analisis lengkap (Excel, satu sheet per langkah)", workbook.read_bytes(),
            file_name=f"{model.name}_{sms.WORKBOOK}",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"sm_dl_xlsx_{model.name}")
    cols[1].download_button(
        "Unduh ringkasan model sumber (CSV)", summary.to_csv(index=False).encode("utf-8"),
        file_name=f"{model.name}_source_model_summary.csv", mime="text/csv",
        key=f"sm_dl_csv_{model.name}")
    st.caption(f"Semua tabel per langkah (CSV) ada di `{model.path.relative_to(sms.ROOT)}`.")


# --- Step 3: GMPE logic tree -------------------------------------------------------

def _lt_key(project: dict[str, Any], trt: str, index: int, field: str) -> str:
    return f"wizard_lt_{project['id']}_{trt}_{index}_{field}"


def _load_lt_into_state(project: dict[str, Any], trts: list[str],
                        logic_tree: dict[str, list[dict]]) -> None:
    for trt in trts:
        chosen = {b["gmpe"]: float(b["weight"]) for b in logic_tree.get(trt, [])}
        for index, gmpe in enumerate(gc.candidates(trt)):
            st.session_state[_lt_key(project, trt, index, "on")] = gmpe.key in chosen
            st.session_state[_lt_key(project, trt, index, "w")] = chosen.get(gmpe.key, 0.0)


def _lt_from_state(project: dict[str, Any], trts: list[str]) -> dict[str, list[dict]]:
    tree = {}
    for trt in trts:
        branches = []
        for index, gmpe in enumerate(gc.candidates(trt)):
            if st.session_state.get(_lt_key(project, trt, index, "on")):
                branches.append({"gmpe": gmpe.key, "weight": round(float(
                    st.session_state.get(_lt_key(project, trt, index, "w"), 0.0)), 4)})
        tree[trt] = branches
    return tree


def _trellis_figure(result: dict, labels: dict[str, str], weights: dict[str, float],
                    mag: float) -> go.Figure:
    figure = go.Figure()
    distances = result["distances"]
    weighted = np.zeros(len(distances))
    total = 0.0
    for entry in result["results"]:
        if entry.get("error"):
            continue
        curve = next(c for c in entry["curves"] if abs(c["mag"] - mag) < 1e-6)
        median = np.asarray(curve["median_g"])
        selected = entry["gmpe"] in weights
        figure.add_trace(go.Scatter(
            x=distances, y=median, mode="lines", name=labels.get(entry["gmpe"], entry["gmpe"]),
            line=dict(width=2.6 if selected else 1.2, dash="solid" if selected else "dot"),
            opacity=1.0 if selected else 0.55))
        if selected:
            weighted += weights[entry["gmpe"]] * np.log(median)
            total += weights[entry["gmpe"]]
    if total > 0:
        figure.add_trace(go.Scatter(
            x=distances, y=np.exp(weighted / total), mode="lines",
            name="Rerata berbobot logic tree", line=dict(color="black", width=3.5)))
    figure.update_layout(
        xaxis=dict(title="Jarak Rjb (km)", type="log"),
        yaxis=dict(title=f"Median {result['imt']} (g)", type="log"),
        height=430, margin=dict(l=10, r=10, t=30, b=10),
        legend=dict(font=dict(size=10)),
        title=dict(text=f"M {mag:g}, Vs30 {result['vs30']:g} m/s", font=dict(size=13)))
    return figure


def _trellis_section(project: dict[str, Any], trt: str, tree: dict[str, list[dict]]) -> None:
    st.markdown("**Grafik trellis**: median guncangan terhadap jarak. Garis tebal = GMPE "
                "terpilih, titik-titik = kandidat lain, hitam = rerata berbobot pilihan Anda.")
    mags = TRELLIS_MAGS.get(trt, (6.0, 7.0, 8.0))
    cols = st.columns([2, 2, 1])
    mag = cols[0].select_slider("Magnitudo", mags, value=mags[1],
                                key=f"wizard_trellis_mag_{project['id']}_{trt}")
    imt = cols[1].selectbox("Parameter", ["PGA", "SA(0.2)", "SA(1.0)"],
                            format_func=lambda v: IMT_LABELS.get(v, v),
                            key=f"wizard_trellis_imt_{project['id']}_{trt}")
    cols[2].write("")
    show = cols[2].button("Hitung trellis", key=f"wizard_trellis_go_{project['id']}_{trt}")
    state_key = f"wizard_trellis_req_{project['id']}_{trt}"
    if show:
        st.session_state[state_key] = imt
    if state_key not in st.session_state:
        st.caption("Tekan **Hitung trellis** untuk membandingkan semua kandidat "
                   "(beberapa detik, dihitung dengan OpenQuake).")
        return
    imt = st.session_state[state_key]
    candidates = gc.candidates(trt)
    request = {"gmpes": [g.key for g in candidates], "trt": trt, "imt": imt,
               "mags": list(mags), "distances": TRELLIS_DISTANCES, "vs30": 760}
    try:
        result = _trellis(json.dumps(request, sort_keys=True))
    except Exception as exc:  # The subprocess reports its own error message.
        st.error(f"Trellis gagal dihitung: {exc}")
        return
    weights = {b["gmpe"]: float(b["weight"]) for b in tree.get(trt, [])
               if float(b["weight"]) > 0}
    labels = {g.key: g.label for g in candidates}
    st.plotly_chart(_trellis_figure(result, labels, weights, mag), use_container_width=True)
    errors = [labels.get(e["gmpe"], e["gmpe"]) for e in result["results"] if e.get("error")]
    if errors:
        st.warning("Tidak dapat dihitung: " + ", ".join(errors))
    geometry = result["geometry"]
    st.caption(
        f"Rupture generik: dip {geometry['dip']:g}°, rake {geometry['rake']:g}°, puncak "
        f"rupture {geometry['ztor']:g} km, hiposenter {geometry['hypo_depth']:g} km; situs di "
        "sisi hanging wall. Grafik ini untuk membandingkan GMPE, bukan perkiraan di lokasi "
        "tertentu.")


def _step_gmpe(project: dict[str, Any]) -> None:
    trts = project["source_model"].get("trts") or _source_trts(
        project["source_model"]["version"])
    st.markdown(
        "**Apa yang diputuskan:** persamaan guncangan tanah (GMPE) untuk setiap tatanan "
        "tektonik, dan bobotnya di logic tree.\n\n"
        "- GMPE memperkirakan guncangan di situs dari magnitudo, jarak, dan kondisi tanah.\n"
        "- Tidak ada satu GMPE yang pasti benar, sehingga dipakai beberapa GMPE dengan "
        "**bobot** (jumlahnya 1 per tatanan tektonik). Bobot mencerminkan seberapa besar "
        "keyakinan Anda pada tiap model.\n"
        "- Peta akhir adalah **rerata** hasil semua cabang logic tree.\n"
        "- Kriteria umum memilih GMPE: tatanan tektonik cocok, rentang magnitudo/jarak "
        "datanya mencakup kasus Anda, model mutakhir dan terpublikasi, dan bila mungkin "
        "sesuai dengan data guncangan regional. Gunakan trellis untuk melihat model yang "
        "menyimpang jauh dari yang lain."
    )
    seeded_key = f"wizard_lt_seeded_{project['id']}"
    pending_key = f"wizard_lt_pending_{project['id']}"
    if not st.session_state.get(seeded_key):
        _load_lt_into_state(project, trts, project.get("gmpe_logic_tree") or {})
        st.session_state[seeded_key] = True
    # Widget values can only be set before the widgets are drawn, so buttons
    # store the new logic tree here and it is applied on the next run.
    if pending_key in st.session_state:
        _load_lt_into_state(project, trts, st.session_state.pop(pending_key))

    cols = st.columns(3)
    for col, name in zip(cols, gc.PRESETS):
        if col.button(f"Isi seperti {name}", key=f"wizard_preset_{project['id']}_{name}"):
            st.session_state[pending_key] = gc.preset(name, trts)
            _rerun()
    if cols[2].button("Kosongkan semua", key=f"wizard_clear_{project['id']}"):
        st.session_state[pending_key] = {}
        _rerun()
    st.caption("Preset hanyalah titik awal; Anda bebas menambah, menghapus, dan mengubah bobot.")

    tabs = st.tabs([gc.TRT_LABELS.get(t, t) for t in trts])
    for tab, trt in zip(tabs, trts):
        with tab:
            st.markdown(gc.TRT_EXPLANATIONS.get(trt, ""))
            header = st.columns([0.6, 3.2, 1.2, 4.0])
            header[1].markdown("**GMPE**")
            header[2].markdown("**Bobot**")
            header[3].markdown("**Data, rentang, catatan**")
            for index, gmpe in enumerate(gc.candidates(trt)):
                row = st.columns([0.6, 3.2, 1.2, 4.0])
                on = row[0].checkbox("pilih", key=_lt_key(project, trt, index, "on"),
                                     label_visibility="collapsed")
                used = (" · dipakai " + ", ".join(gmpe.used_by)) if gmpe.used_by else ""
                row[1].markdown(f"{gmpe.label}{used}")
                row[2].number_input("bobot", min_value=0.0, max_value=1.0, step=0.01,
                                    format="%.2f", key=_lt_key(project, trt, index, "w"),
                                    disabled=not on, label_visibility="collapsed")
                row[3].caption(f"{gmpe.data}; {gmpe.ranges}. {gmpe.notes}")
            if st.button("Bagi bobot sama rata untuk GMPE terpilih",
                         key=f"wizard_equal_{project['id']}_{trt}"):
                chosen = [g.key for i, g in enumerate(gc.candidates(trt))
                          if st.session_state.get(_lt_key(project, trt, i, "on"))]
                current = _lt_from_state(project, trts)
                current[trt] = gc.equal_weights(chosen)
                st.session_state[pending_key] = current
                _rerun()
            tree = _lt_from_state(project, trts)
            total = sum(b["weight"] for b in tree.get(trt, []))
            ok = abs(total - 1) <= gc.WEIGHT_TOLERANCE
            st.markdown(f"Jumlah bobot: **{total:.2f}**".replace(".", ",")
                        + (" ✅" if ok else " ❌ harus 1,00"))
            with st.expander("Bandingkan GMPE (trellis)", expanded=False):
                _trellis_section(project, trt, tree)

    tree = _lt_from_state(project, trts)
    check = gc.validate(tree, trts)
    for message in check.errors:
        st.error(message)
    for message in check.warnings:
        st.warning(message)
    st.markdown(
        f"Logic tree ini memiliki **{gc.realizations(tree, trts):,}** kombinasi cabang "
        f"(realisasi) dari **{gc.gmpe_count(tree)}** GMPE.".replace(",", "."))
    note = _notes(project, "gmpe", "Catatan: alasan pemilihan GMPE dan bobot")
    if _confirm_button(project, "gmpe", "Konfirmasi logic tree GMPE", disabled=not check.ok):
        project["gmpe_logic_tree"] = tree
        project.setdefault("notes", {})["gmpe"] = note
        _commit(project, "gmpe")


# --- Step 4: review and run -------------------------------------------------------------

def _logic_tree_table(tree: dict[str, list[dict]]) -> pd.DataFrame:
    rows = []
    for trt, branches in tree.items():
        for branch in branches:
            gmpe = gc.by_key(branch["gmpe"])
            rows.append({"Tatanan tektonik": gc.TRT_LABELS.get(trt, trt),
                         "GMPE": gmpe.label if gmpe else branch["gmpe"],
                         "Bobot": f"{branch['weight']:.2f}".replace(".", ",")})
    return pd.DataFrame(rows)


def _project_runs(project: dict[str, Any], results_dir: Path) -> list:
    return [job for job in list_jobs(results_dir)
            if job.info.get("project_id") == project["id"]]


def _step_review(project: dict[str, Any], results_dir: Path) -> None:
    region = project["region"]
    version = project["source_model"]["version"]
    trts = project["source_model"].get("trts") or _source_trts(version)
    tree = project["gmpe_logic_tree"]
    sites = _region_sites(region)
    n_gmpes = gc.gmpe_count(tree)
    estimate = estimate_runtime_seconds(len(sites), n_gmpes)

    st.markdown("**Ringkasan seluruh pilihan.** Periksa sekali lagi sebelum menghitung; "
                "semua ini akan dicatat di manifest hasil.")
    cols = st.columns(4)
    cols[0].metric("Situs", f"{len(sites):,}".replace(",", "."))
    cols[1].metric("GMPE", n_gmpes)
    cols[2].metric("Realisasi logic tree",
                   f"{gc.realizations(tree, trts):,}".replace(",", "."))
    cols[3].metric("Perkiraan waktu", format_duration(estimate))
    st.markdown(
        f"- **Wilayah:** {escape_html(_region_label(region))}, grid "
        f"{region['spacing']:g}°, {_vs30_text(region)}\n"
        f"- **Model sumber:** {escape_html(_source_label(project['source_model']))}\n"
        "- **Keluaran:** kurva hazard rerata dan peta PGA, SA 0,2, SA 1,0 untuk 475 dan "
        "2.475 tahun")
    st.dataframe(_logic_tree_table(tree), hide_index=True, use_container_width=True)
    notes = {s: t for s, t in project.get("notes", {}).items() if t.strip()}
    if notes:
        with st.expander("Catatan Anda per langkah"):
            for step, text in notes.items():
                st.markdown(f"**{pp.STEP_TITLES.get(step, step)}**: {escape_html(text)}")

    running = active_job(results_dir)
    mine = _project_runs(project, results_dir)
    if running is not None and running.info.get("project_id") == project["id"]:
        _monitor(results_dir)
        return
    finished = [job for job in mine if job.state == "finished"]
    if finished:
        st.success(f"Perhitungan selesai: run `{finished[0].run_id}`. Buka langkah 5.")
    failed = [job for job in mine if job.state == "failed"]
    if failed and not finished:
        st.error(f"Run `{failed[0].run_id}` gagal. Log terakhir:")
        st.code(log_tail(failed[0], lines=20) or "(log kosong)", language=None)

    if not oq_available():
        st.error("Lingkungan OpenQuake (.venv-oq) tidak ditemukan di komputer ini.")
        return
    memory = memory_percent()
    if memory is not None and memory >= OQ_MEMORY_LIMIT_PERCENT:
        st.warning(
            f"RAM terpakai {memory:.0f}%. OpenQuake baru mulai menghitung bila pemakaian "
            f"di bawah {OQ_MEMORY_LIMIT_PERCENT}%; tutup aplikasi lain (browser, aplikasi "
            "chat) sebelum atau sesudah menekan tombol. Perhitungan akan menunggu dan "
            "lanjut sendiri.")
    if running is not None:
        st.info(f"Run lain (`{running.run_id}`) sedang berjalan. Tunggu sampai selesai.")
        return
    reviewed = st.checkbox(
        "Saya sudah meninjau wilayah, model sumber, dan logic tree GMPE di atas.",
        key=f"wizard_reviewed_{project['id']}")
    if st.button("Mulai perhitungan PSHA", type="primary", disabled=not reviewed,
                 key=f"wizard_start_{project['id']}"):
        run_id = f"{project['id']}_run{len(mine) + 1}"
        source = project["source_model"]
        own = sms.ROOT / source["path"] if source.get("kind") == "own" else None
        box = region.get("bounds") if not region.get("provinces") else None
        try:
            start_job(run_id, region["provinces"], float(region["spacing"]), len(sites),
                      results_dir, model_version=version, gmm_logic_tree=tree,
                      project_id=project["id"], kabupaten=region.get("kabupaten", []),
                      bounds=box, source_model=own,
                      vs30_source=region.get("vs30_source", "reference"))
        except (PSHAJobError, OSError) as exc:
            st.error(f"Perhitungan tidak dapat dimulai: {exc}")
            return
        project["run_id"] = run_id
        pp.confirm_step(project, "review")
        _save(project)
        _rerun()


@st.fragment(run_every=10)
def _monitor(results_dir: Path) -> None:
    job = active_job(results_dir)
    if job is None:
        st.rerun(scope="app")
        return
    elapsed = job.elapsed_seconds()
    estimate = float(job.info["estimated_seconds"])
    progress = latest_progress(job)
    stage = f"tahap {progress[0]} {progress[1]}%" if progress else "menyiapkan model"
    st.markdown(f"**Sedang menghitung `{job.run_id}`**")
    st.progress(min(elapsed / estimate, 0.99) if estimate > 0 else 0.0,
                text=f"{format_duration(elapsed)} dari perkiraan {format_duration(estimate)} "
                     f"· {stage}")
    memory = waiting_for_memory(job)
    if memory is not None:
        st.warning(f"OpenQuake menunggu: RAM terpakai {memory}%, perlu di bawah "
                   f"{OQ_MEMORY_LIMIT_PERCENT}%. Tutup aplikasi lain; perhitungan "
                   "lanjut sendiri.")
    st.code(log_tail(job) or "(log belum ada)", language=None)
    if st.button("Hentikan perhitungan", key=f"wizard_cancel_{job.run_id}"):
        cancel_job(job)
        st.rerun(scope="app")


# --- Step 5: result ---------------------------------------------------------------------

def _step_result(project: dict[str, Any], results_dir: Path) -> None:
    run_id = project.get("run_id")
    runs = {run.run_id: run for run in list_psha_runs(results_dir)}
    run = runs.get(run_id)
    if run is None:
        st.info("Hasil belum tersedia. Perhitungan masih berjalan atau belum dimulai "
                "(lihat langkah 4).")
        return
    if not pp.is_confirmed(project, "result"):
        pp.confirm_step(project, "result")
        _save(project)
    try:
        hazard = load_hazard_map(run)
    except PSHAResultError as exc:
        st.error(str(exc))
        return
    st.success(f"Run `{run.run_id}` selesai ({len(hazard)} situs, "
               f"{format_duration(run.manifest.get('runtime_seconds', 0))}).")
    rows = []
    for column in run.columns:
        values = hazard[column.column]
        rows.append({"Parameter": IMT_LABELS.get(column.imt, column.imt),
                     "Periode ulang": f"{column.return_period_years:,} th".replace(",", "."),
                     "Min (g)": round(values.min(), 3),
                     "Median (g)": round(values.median(), 3),
                     "Maks (g)": round(values.max(), 3)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    st.markdown(
        f"Peta lengkap, kurva hazard per titik, dan unduhan ada di tab **Bahaya Gempa "
        f"(PSHA)**: pilih hasil `{run.run_id}`. Model sumber: {model_label(run.manifest)}, "
        "dengan logic tree GMPE pilihan Anda.")

    others = [r for r in runs.values() if r.run_id != run.run_id
              and r.manifest["region"] == run.manifest["region"]]
    if not others:
        st.caption("Untuk membandingkan, hitung run lain di wilayah dan grid yang sama "
                   "(misalnya proyek kedua dengan logic tree berbeda).")
        return
    st.markdown("**Bandingkan dengan run lain di wilayah yang sama**")
    other = st.selectbox("Run pembanding", others, format_func=lambda r: r.run_id,
                         key=f"wizard_compare_{project['id']}")
    column = st.selectbox("Parameter", run.columns,
                          format_func=lambda c: f"{c.imt} {c.return_period_years} th",
                          key=f"wizard_compare_col_{project['id']}")
    merged = hazard.merge(load_hazard_map(other), on=["lon", "lat"], suffixes=("", "_other"))
    if column.column in merged and f"{column.column}_other" in merged:
        ratio = merged[column.column] / merged[f"{column.column}_other"]
        cols = st.columns(3)
        cols[0].metric("Median rasio", f"{ratio.median():.2f}".replace(".", ","))
        cols[1].metric("Rasio min", f"{ratio.min():.2f}".replace(".", ","))
        cols[2].metric("Rasio maks", f"{ratio.max():.2f}".replace(".", ","))
        st.caption(f"Rasio = {run.run_id} ÷ {other.run_id}. Rasio > 1 berarti run ini "
                   "menghasilkan bahaya lebih tinggi.")


# --- Entry point ------------------------------------------------------------------------

STEP_RENDERERS = {
    "region": lambda project, _: _step_region(project),
    "source": lambda project, _: _step_source(project),
    "gmpe": lambda project, _: _step_gmpe(project),
    "review": _step_review,
    "result": _step_result,
}


@st.fragment
def render_psha_wizard(results_dir: Path) -> None:
    # A fragment: clicks inside the wizard rerun only the wizard, not the
    # whole (map-heavy) app.
    st.markdown(
        "Bangun model PSHA langkah demi langkah. Setiap langkah menjelaskan apa yang "
        "diputuskan dan harus Anda **konfirmasi** sebelum langkah berikutnya terbuka; "
        "perhitungan baru bisa dimulai setelah semua pilihan ditinjau.")
    project = _project_selector()
    if project is None:
        return
    _progress_bar(project)
    current = pp.current_step(project)
    step = st.radio(
        "Langkah", pp.STEPS, index=pp.STEPS.index(current), horizontal=True,
        format_func=lambda s: pp.STEP_TITLES[s] + ("" if pp.unlocked(project, s) else " 🔒"),
        key=f"wizard_step_{project['id']}_{current}")
    st.divider()
    st.subheader(pp.STEP_TITLES[step])
    if not pp.unlocked(project, step):
        missing = [pp.STEP_TITLES[s] for s in pp.STEPS[:pp.STEPS.index(step)]
                   if not pp.is_confirmed(project, s)]
        st.info("Langkah ini terkunci. Konfirmasi dulu: " + ", ".join(missing) + ".")
        return
    STEP_RENDERERS[step](project, results_dir)
