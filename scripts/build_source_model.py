"""Build a Tahap B source model (catalogue part) for a box, with a CSV per step.

Needs the OpenQuake environment (HMTK) and ``data/psha/catalog/mw_catalog.csv``
(``scripts/build_mw_catalog.py``)::

    .venv-oq\\Scripts\\python.exe scripts\\build_source_model.py --name ntt \\
        --bounds 118.8 -11.1 125.2 -8.0

``--bounds`` (west south east north) is the box selected in the app: it
defines the hazard sites. Earthquakes are taken from the box plus
``--buffer-km`` because sources outside the box still shake sites inside it.
Steps and outputs in ``data/psha/source_models/<name>/`` (``00_files.csv``
describes every file):

1. Region (``01_region.csv``)
2. Catalogue selection (``02_catalog_selected.csv``); events newer than the ISC
   review lag are left out unless ``--include-unreviewed``
3. Gardner-Knopoff declustering (``03_*``) on all years; ``--start-year`` and
   ``--end-year`` then limit the mainshocks used from step 4 on
4. Stepp completeness on the declustered catalogue (``04_*``)
5. Weichert a and b per depth layer (``05_*``)
6. Smoothed background seismicity per layer (``06_*``, Frankel 1995)
7. PuSGeN 2024 faults and megathrust near the box (``07_*``)
8. OpenQuake files: ``nrml/*.xml`` and the source logic tree ``ssmLT.xml``

``source_model_summary.csv`` has one row per source and
``analisis_model_sumber.xlsx`` holds every table as a sheet. Run the hazard
with ``scripts/run_psha.py --source-model data/psha/source_models/<name>``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import warnings

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.analysis_workbook import write_workbook  # noqa: E402
from utils.source_model import (  # noqa: E402
    DEFAULT_DEPTH_EDGES,
    GRID_SPACING_DEG,
    MIN_EVENTS_FOR_B,
    SMOOTHING_KM,
    layer_names,
    smoothed_grid,
    cluster_table,
    completeness_rows,
    declustering_events,
    declustering_summary,
    depth_layer_labels,
    expand_bounds,
    fmd_rows,
    limit_years,
    region_warnings,
    select_catalog,
    stepp_rows,
)

CATALOG_DIR = ROOT / "data" / "psha" / "catalog"
OUT_ROOT = ROOT / "data" / "psha" / "source_models"
ALL_DEPTHS = "semua kedalaman"
WORKBOOK = "analisis_model_sumber.xlsx"
FILES = {
    "01_region.csv": "Kotak situs, buffer, dan wilayah sumber gempa",
    "02_catalog_selected.csv": "Gempa di wilayah sumber dengan Mw >= batas declustering, "
                               "beserta lapisan kedalamannya",
    "03_declustering_events.csv": "Setiap gempa: nomor klaster, peran (utama/susulan/"
                                  "pendahulu), jendela Gardner-Knopoff, gempa utamanya",
    "03_declustering_summary.csv": "Jumlah gempa dipertahankan dan dibuang per rentang Mw",
    "03_declustering_clusters.csv": "Satu baris per klaster: gempa utama, jumlah susulan, "
                                    "rentang waktu",
    "03_catalog_mw_declustered.csv": "Katalog akhir: hanya gempa utama/independen di "
                                     "rentang tahun yang dipilih",
    "04_completeness.csv": "Tahun mulai lengkap untuk setiap rentang Mw (Stepp, 1972)",
    "04_stepp_diagram.csv": "Data diagram Stepp: sigma_lambda per jendela waktu",
    "05_fmd.csv": "Distribusi frekuensi-magnitudo per lapisan: laju teramati dan laju G-R",
    "05_ab_values.csv": "Nilai a dan b (Weichert) per lapisan kedalaman",
    "06_smoothed_seismicity.csv": "Gempa latar yang dihaluskan (Frankel, 1995): laju dan "
                                  "nilai a per titik grid per lapisan",
    "07_faults_megathrust.csv": "Sesar dan megathrust PuSGeN 2024 yang dekat kotak situs: "
                                "jarak, rentang magnitudo, laju",
    "source_model_summary.csv": "Ringkasan model sumber: satu baris per sumber",
    "ssmLT.xml": "Logic tree model sumber untuk OpenQuake (dua cabang segmentasi megathrust)",
    "nrml/": "Berkas sumber OpenQuake (gempa latar per lapisan, sesar, megathrust)",
    WORKBOOK: "Semua tabel di atas dalam satu workbook Excel, satu sheet per langkah",
    "manifest.json": "Pengaturan, masukan (SHA-256), dan jumlah di setiap langkah",
}


def catalogue_for_hmtk(frame: pd.DataFrame):
    """HMTK Catalogue from catalogue rows (time parsed to its integer fields)."""
    from openquake.hmtk.seismicity.catalogue import Catalogue

    t = pd.to_datetime(frame["time"], utc=True, format="ISO8601")
    data = {
        "eventID": np.arange(len(frame)),
        "year": t.dt.year.to_numpy(), "month": t.dt.month.to_numpy(),
        "day": t.dt.day.to_numpy(), "hour": t.dt.hour.to_numpy(),
        "minute": t.dt.minute.to_numpy(),
        "second": (t.dt.second + t.dt.microsecond / 1e6).to_numpy(dtype=float),
        "longitude": frame["longitude"].to_numpy(dtype=float),
        "latitude": frame["latitude"].to_numpy(dtype=float),
        "depth": frame["depth_km"].fillna(0).to_numpy(dtype=float),
        "magnitude": frame["mw"].to_numpy(dtype=float),
        "sigmaMagnitude": frame["mw_sigma"].to_numpy(dtype=float),
    }
    return Catalogue.make_from_dict(data)


def decluster(frame: pd.DataFrame, fs_time_prop: float):
    from openquake.hmtk.seismicity.declusterer.dec_gardner_knopoff import GardnerKnopoffType1
    from openquake.hmtk.seismicity.declusterer.distance_time_windows import GardnerKnopoffWindow

    config = {"time_distance_window": GardnerKnopoffWindow(), "fs_time_prop": fs_time_prop}
    return GardnerKnopoffType1().decluster(catalogue_for_hmtk(frame), config)


def stepp(frame: pd.DataFrame, mag_bin: float, time_bin: float):
    from openquake.hmtk.seismicity.completeness.comp_stepp_1971 import Stepp1971

    model = Stepp1971()
    table = model.completeness(catalogue_for_hmtk(frame), {
        "magnitude_bin": mag_bin, "time_bin": time_bin, "increment_lock": True})
    return model, table


def weichert(frame: pd.DataFrame, table: np.ndarray, dmag: float):
    """(b, sigma_b, a, sigma_a), or NaNs when the fit fails."""
    from openquake.hmtk.seismicity.occurrence.weichert import Weichert

    if len(frame) < 2:
        return (np.nan,) * 4
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return Weichert().calc(catalogue_for_hmtk(frame),
                                   {"magnitude_interval": dmag}, completeness=table)
        except (ValueError, IndexError, ZeroDivisionError):
            return (np.nan,) * 4


def rounded(value: float, digits: int):
    return round(float(value), digits) if np.isfinite(value) else np.nan


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--name", required=True, help="folder under data/psha/source_models")
    parser.add_argument("--bounds", nargs=4, type=float, required=True,
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"), help="site box (degrees)")
    parser.add_argument("--buffer-km", type=float, default=300.0,
                        help="earthquakes are taken this far around the box")
    parser.add_argument("--min-mw", type=float, default=4.5,
                        help="smallest Mw used for rates (a and b)")
    parser.add_argument("--decluster-min-mw", type=float, default=4.0,
                        help="smallest Mw used when identifying clusters")
    parser.add_argument("--include-unreviewed", action="store_true",
                        help="keep events newer than the ISC review lag")
    parser.add_argument("--start-year", type=int,
                        help="first year of mainshocks used for rates (default: all)")
    parser.add_argument("--end-year", type=int,
                        help="last year of mainshocks used for rates (default: all)")
    parser.add_argument("--depth-edges", nargs="+", type=float, default=list(DEFAULT_DEPTH_EDGES))
    parser.add_argument("--fs-time-prop", type=float, default=1.0,
                        help="foreshock window as a fraction of the aftershock window")
    parser.add_argument("--stepp-mag-bin", type=float, default=0.5)
    parser.add_argument("--stepp-time-bin", type=float, default=5.0)
    parser.add_argument("--fmd-bin", type=float, default=0.1)
    parser.add_argument("--smoothing-km", type=float, default=SMOOTHING_KM,
                        help="Gaussian smoothing distance of the background seismicity")
    parser.add_argument("--grid-spacing", type=float, default=GRID_SPACING_DEG,
                        help="background seismicity grid (degrees)")
    parser.add_argument("--mmax-shallow", type=float, default=6.5,
                        help="Mmax of the shallow background (larger events are on faults)")
    parser.add_argument("--mmax-slab", type=float, default=7.8,
                        help="Mmax of the intraslab background")
    parser.add_argument("--interface-km", type=float, default=1000.0,
                        help="megathrust sources are kept this far from the box")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", args.name):
        raise SystemExit("--name may only contain letters, digits, _ and -")
    west, south, east, north = args.bounds
    if not (west < east and south < north):
        raise SystemExit("--bounds must be WEST SOUTH EAST NORTH with west < east, south < north")
    if args.start_year is not None and args.end_year is not None \
            and args.start_year > args.end_year:
        raise SystemExit("--start-year must not be after --end-year")
    edges = tuple(args.depth_edges)

    cat_path = CATALOG_DIR / "mw_catalog.csv"
    cat_manifest = json.loads((CATALOG_DIR / "manifest.json").read_text(encoding="utf-8"))
    catalog = pd.read_csv(cat_path, dtype={"isc_evid": str})
    out = OUT_ROOT / args.name
    out.mkdir(parents=True, exist_ok=True)
    counts: dict = {}

    # 1. Region
    source_bounds = expand_bounds(tuple(args.bounds), args.buffer_km)
    notes = region_warnings(source_bounds, cat_manifest["region"])
    region_rows = [
        ("site_west", west, "Batas barat kotak situs (derajat)"),
        ("site_south", south, "Batas selatan kotak situs"),
        ("site_east", east, "Batas timur kotak situs"),
        ("site_north", north, "Batas utara kotak situs"),
        ("buffer_km", args.buffer_km, "Jarak tambahan di sekeliling kotak untuk sumber gempa"),
        ("source_west", source_bounds[0], "Batas barat wilayah sumber"),
        ("source_south", source_bounds[1], "Batas selatan wilayah sumber"),
        ("source_east", source_bounds[2], "Batas timur wilayah sumber"),
        ("source_north", source_bounds[3], "Batas utara wilayah sumber"),
    ] + [("warning", note, "") for note in notes]
    pd.DataFrame(region_rows, columns=["item", "value", "description"]).to_csv(
        out / "01_region.csv", index=False)

    # 2. Catalogue selection
    selected, sel = select_catalog(catalog, source_bounds, args.decluster_min_mw,
                                   args.include_unreviewed)
    counts["selection"] = sel
    if selected.empty:
        raise SystemExit("No earthquakes in the source region.")
    selected["depth_layer"] = depth_layer_labels(selected["depth_km"], edges)
    selected.to_csv(out / "02_catalog_selected.csv", index=False)

    # 3. Declustering on every year, then the chosen years
    cluster, flag = decluster(selected, args.fs_time_prop)
    events = declustering_events(selected, cluster, flag)
    events.to_csv(out / "03_declustering_events.csv", index=False)
    declustering_summary(events).to_csv(out / "03_declustering_summary.csv", index=False)
    cluster_table(events).to_csv(out / "03_declustering_clusters.csv", index=False)
    mains = events.loc[events["kept"], list(selected.columns)].reset_index(drop=True)
    counts["declustering"] = {"events": int(len(events)), "mainshocks": int(len(mains)),
                              "removed": int(len(events) - len(mains)),
                              "clusters": int(events.loc[events["cluster_id"] > 0,
                                                         "cluster_id"].nunique())}
    mains, counts["period"] = limit_years(mains, args.start_year, args.end_year)
    if mains.empty:
        raise SystemExit("No mainshocks between --start-year and --end-year.")
    mains.to_csv(out / "03_catalog_mw_declustered.csv", index=False)
    # The period ends with the last catalogue year, or earlier with --end-year.
    end_year = int(selected["year"].max())
    if args.end_year is not None:
        end_year = min(end_year, args.end_year)

    # 4. Completeness on the declustered catalogue (all depths)
    model, table = stepp(mains, args.stepp_mag_bin, args.stepp_time_bin)
    table[:, 0] = np.clip(table[:, 0], int(mains["year"].min()), end_year)
    completeness_rows(table, args.stepp_mag_bin, mains["mw"], mains["year"], end_year).to_csv(
        out / "04_completeness.csv", index=False)
    stepp_rows(model.time_values, model.sigma, model.model_line, model.magnitude_bin,
               model.end_year).to_csv(out / "04_stepp_diagram.csv", index=False)
    # Rates start at --min-mw with the completeness year of the Stepp bin holding it.
    holding = table[table[:, 1] <= args.min_mw + 1e-9]
    start_year = holding[-1, 0] if holding.size else table[0, 0]
    rate_table = np.vstack([[start_year, args.min_mw],
                            table[table[:, 1] > args.min_mw + 1e-9]])

    # 5. a and b per depth layer, all depths together and (not modelled) below the layers
    layers = layer_names(edges)
    deep = f">{edges[-1]:g} km"
    fmd_frames, ab_rows, ab_by_layer = [], [], {}
    for layer in [ALL_DEPTHS] + layers + [deep]:
        part = mains if layer == ALL_DEPTHS else mains[mains["depth_layer"] == layer]
        part = part[part["mw"] >= args.min_mw - 1e-9]
        b, sigma_b, a, sigma_a = weichert(part, rate_table, args.fmd_bin)
        fmd = fmd_rows(part["mw"], part["year"], rate_table, end_year, args.fmd_bin,
                       args.min_mw, a, b)
        if not fmd.empty:
            fmd_frames.append(fmd.assign(depth_layer=layer))
        n_used = int(fmd["n_obs"].sum()) if not fmd.empty else 0
        reliable = bool(n_used >= MIN_EVENTS_FOR_B and np.isfinite(b))
        rate5 = 10 ** (a - b * 5.0) if np.isfinite(a) and np.isfinite(b) else np.nan
        ab = {"depth_layer": layer, "n_events": int(len(part)), "n_used_complete": n_used,
              "mmin": args.min_mw, "b_value": rounded(b, 3), "b_sigma": rounded(sigma_b, 3),
              "a_value": rounded(a, 3), "a_sigma": rounded(sigma_a, 3),
              "rate_mw_ge_5_per_year": rounded(rate5, 4),
              "mw_max_observed": float(part["mw"].max()) if len(part) else np.nan,
              "reliable": reliable,
              "note": "" if reliable else
              f"kurang dari {MIN_EVENTS_FOR_B} gempa di periode lengkap atau fit gagal"}
        if layer == deep:
            ab["note"] = f"di bawah {edges[-1]:g} km: tidak dimodelkan (seperti GEM)"
        ab_rows.append(ab)
        ab_by_layer[layer] = ab
    if fmd_frames:
        fmd_all = pd.concat(fmd_frames, ignore_index=True)
        fmd_all[["depth_layer"] + [c for c in fmd_all.columns if c != "depth_layer"]].to_csv(
            out / "05_fmd.csv", index=False)
    pd.DataFrame(ab_rows).to_csv(out / "05_ab_values.csv", index=False)

    # 6. Smoothed background seismicity per modelled layer -> NRML
    from utils.source_model_oq import (
        SEGMENTATION_WEIGHTS, background_source, gem_layer_templates, hypo_depth, mfd_summary,
        select_faults, template_for_layer, write_logic_tree, write_sources)

    nrml_dir = out / "nrml"
    nrml_dir.mkdir(exist_ok=True)
    templates = gem_layer_templates()
    regional = ab_by_layer[ALL_DEPTHS]
    years_at_mmin = end_year - int(start_year) + 1
    smooth_frames, summary_rows, background_files = [], [], []
    for layer, top, bottom in zip(layers, edges[:-1], edges[1:]):
        ab = ab_by_layer[layer]
        part = mains[(mains["depth_layer"] == layer) & (mains["mw"] >= args.min_mw - 1e-9)
                     & (mains["year"] >= start_year)]
        if ab["reliable"]:
            b, b_source = ab["b_value"], "lapisan ini (Weichert)"
            total = 10 ** (ab["a_value"] - b * args.min_mw)
        else:
            b, b_source = regional["b_value"], "regional (semua kedalaman)"
            total = len(part) / years_at_mmin
        template = template_for_layer(templates, top, bottom)
        shallow = template.tectonic_region_type == "Active Shallow Crust"
        mmax = args.mmax_shallow if shallow else args.mmax_slab
        source_id = "bg_" + re.sub(r"[^0-9]+", "_", layer).strip("_")
        row = {"source_id": source_id,
               "source_type": "gempa latar (smoothed seismicity, Frankel 1995)",
               "tectonic_region": template.tectonic_region_type, "depth_layer": layer,
               "hypo_depth_km": hypo_depth(template), "n_sources": 0,
               "n_events_declustered": ab["n_events"], "n_used_complete": ab["n_used_complete"],
               "mmin": args.min_mw, "mmax": mmax, "mw_max_observed": ab["mw_max_observed"],
               "complete_from_year_at_mmin": int(start_year), "end_year": end_year,
               "b_value": b, "b_sigma": ab["b_sigma"] if ab["reliable"] else regional["b_sigma"],
               "b_source": b_source, "annual_rate_mw_ge_mmin": round(total, 4),
               "rate_mw_ge_5_per_year": round(total * 10 ** (-b * (5.0 - args.min_mw)), 4),
               "rate_mw_ge_7_per_year": np.nan, "data_source": "katalog Mw gabungan (ISC/ISC-GEM)",
               "file": "", "note": ab["note"]}
        if part.empty or total <= 0:
            row["note"] = "tidak ada gempa utama di lapisan ini; sumber tidak dibuat"
            summary_rows.append(row)
            continue
        grid = smoothed_grid(part["longitude"], part["latitude"], source_bounds, total,
                             args.grid_spacing, args.smoothing_km)
        grid = grid[grid["annual_rate"] >= total * 1e-7].reset_index(drop=True)
        grid["a_value"] = np.log10(grid["annual_rate"]) + b * args.min_mw
        src = background_source(template, source_id, f"Gempa latar {layer}", grid["longitude"],
                                grid["latitude"], grid["a_value"], b, args.min_mw, mmax)
        filename = f"{source_id}.xml"
        write_sources(nrml_dir / filename, [src], f"Gempa latar {layer} ({args.name})")
        background_files.append(f"nrml/{filename}")
        smooth_frames.append(grid.assign(depth_layer=layer, source_id=source_id))
        row.update({"n_sources": int(len(grid)), "file": f"nrml/{filename}",
                    "rate_mw_ge_7_per_year": round(total * 10 ** (-b * (7.0 - args.min_mw))
                                                   if mmax > 7.0 else 0.0, 5)})
        summary_rows.append(row)
    if smooth_frames:
        smooth = pd.concat(smooth_frames, ignore_index=True)
        smooth[["source_id", "depth_layer", "longitude", "latitude", "count_raw", "weight",
                "annual_rate", "a_value"]].to_csv(out / "06_smoothed_seismicity.csv",
                                                  index=False)

    # 7. Faults and megathrust of PuSGeN 2024 (GEM IDN v2024) near the box -> NRML
    faults = select_faults(tuple(args.bounds), args.buffer_km, args.interface_km)
    fault_rows, crust_files, interface_files = [], [], {}
    labels = {"crust": "sesar aktif kerak dangkal",
              "interface_S1": "megathrust, segmentasi S1",
              "interface_S2": "megathrust, segmentasi S2"}
    for group, kept in faults.items():
        sources = [src for src, _ in kept]
        info = [dict(mfd_summary(src), distance_km=round(dist, 1)) for src, dist in kept]
        for src, extra in zip(sources, info):
            fault_rows.append({"group": group, "source_id": src.source_id, "name": src.name,
                               "tectonic_region": src.tectonic_region_type,
                               "source_class": type(src).__name__, **extra})
        files = []
        if sources:
            filename = f"{group}.xml"
            write_sources(nrml_dir / filename, sources, f"{labels[group]} ({args.name})")
            files = [f"nrml/{filename}"]
        if group == "crust":
            crust_files = files
        else:
            interface_files[group] = files
        weight = SEGMENTATION_WEIGHTS.get(group)
        summary_rows.append({
            "source_id": group, "source_type": labels[group] + (
                f" (bobot cabang {weight})" if weight else ""),
            "tectonic_region": sources[0].tectonic_region_type if sources else "",
            "depth_layer": "", "hypo_depth_km": np.nan, "n_sources": len(sources),
            "mmin": min((i["mag_min"] for i in info), default=np.nan),
            "mmax": max((i["mag_max"] for i in info), default=np.nan),
            "annual_rate_mw_ge_mmin": round(sum(i["annual_rate_total"] for i in info), 4),
            "rate_mw_ge_7_per_year": round(sum(i["annual_rate_mw_ge_7"] for i in info), 5),
            "data_source": "PuSGeN 2024 via GEM IDN v2024 (geometri dan laju dari slip-rate)",
            "file": ";".join(files),
            "note": (f"dalam {args.interface_km:g} km dari kotak situs"
                     if group.startswith("interface") else
                     f"dalam {args.buffer_km:g} km dari kotak situs")})
    pd.DataFrame(fault_rows).to_csv(out / "07_faults_megathrust.csv", index=False)

    # 8. Source logic tree for OpenQuake
    write_logic_tree(out / "ssmLT.xml", background_files, crust_files, interface_files)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(out / "source_model_summary.csv", index=False)
    files_table = pd.DataFrame([{"file": name, "description": text}
                                for name, text in FILES.items()])
    files_table.to_csv(out / "00_files.csv", index=False)

    manifest = {
        "name": args.name,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "site_bounds": args.bounds, "buffer_km": args.buffer_km,
        "source_bounds": list(source_bounds), "warnings": notes,
        "catalog": {"path": "data/psha/catalog/mw_catalog.csv", "sha256": sha256(cat_path),
                    "created_utc": cat_manifest["created_utc"]},
        "settings": {"min_mw_rates": args.min_mw, "decluster_min_mw": args.decluster_min_mw,
                     "include_unreviewed": args.include_unreviewed,
                     "start_year_limit": args.start_year, "end_year_limit": args.end_year,
                     "declustering": "Gardner & Knopoff (1974), HMTK GardnerKnopoffType1",
                     "fs_time_prop": args.fs_time_prop,
                     "completeness": "Stepp (1972), HMTK Stepp1971, increment_lock",
                     "stepp_mag_bin": args.stepp_mag_bin, "stepp_time_bin": args.stepp_time_bin,
                     "recurrence": "Weichert (1980), HMTK", "fmd_bin": args.fmd_bin,
                     "depth_edges_km": args.depth_edges,
                     "smoothing": "Frankel (1995) Gaussian kernel, truncated at 3c",
                     "smoothing_km": args.smoothing_km, "grid_spacing_deg": args.grid_spacing,
                     "mmax_shallow_background": args.mmax_shallow,
                     "mmax_intraslab_background": args.mmax_slab,
                     "rupture_settings": "from GEM IDN v2024 gridded layers",
                     "faults_and_megathrust": "PuSGeN 2024 via GEM IDN v2024",
                     "fault_distance_km": args.buffer_km,
                     "interface_distance_km": args.interface_km},
        "end_year": end_year,
        "counts": counts,
        "openquake_files": {"source_logic_tree": "ssmLT.xml",
                            "background": background_files, "crust": crust_files,
                            "interface": interface_files},
        "status": "siap dijalankan di OpenQuake (scripts/run_psha.py --source-model)",
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                                       encoding="utf-8")

    # One workbook with every step as a sheet (the CSVs above stay as well).
    def sheet(name: str) -> pd.DataFrame:
        path = out / name
        return pd.read_csv(path) if path.is_file() else pd.DataFrame()

    settings = pd.DataFrame([{"item": key, "value": json.dumps(value, ensure_ascii=False)
                              if isinstance(value, (list, dict)) else value}
                             for key, value in {**manifest["settings"],
                                                "site_bounds": args.bounds,
                                                "source_bounds": list(source_bounds),
                                                "end_year": end_year,
                                                "warnings": notes}.items()])
    write_workbook(out / WORKBOOK, {
        "00 Daftar file": files_table,
        "00 Pengaturan": settings,
        "01 Wilayah": sheet("01_region.csv"),
        "02 Katalog terpilih": sheet("02_catalog_selected.csv"),
        "03a Declustering per gempa": sheet("03_declustering_events.csv"),
        "03b Declustering ringkasan": sheet("03_declustering_summary.csv"),
        "03c Klaster": sheet("03_declustering_clusters.csv"),
        "03d Katalog declustered": sheet("03_catalog_mw_declustered.csv"),
        "04a Kelengkapan": sheet("04_completeness.csv"),
        "04b Diagram Stepp": sheet("04_stepp_diagram.csv"),
        "05a Frekuensi-magnitudo": sheet("05_fmd.csv"),
        "05b Nilai a-b": sheet("05_ab_values.csv"),
        "06 Smoothed seismicity": sheet("06_smoothed_seismicity.csv"),
        "07 Sesar dan megathrust": sheet("07_faults_megathrust.csv"),
        "08 Ringkasan model sumber": summary,
    })

    print(f"Source region {source_bounds}; {sel['selected']} events selected")
    for note in notes:
        print("WARNING:", note)
    print(f"Declustering: {counts['declustering']}")
    print(f"Years: {counts['period']}")
    print(pd.read_csv(out / "04_completeness.csv").to_string(index=False))
    print(summary[["source_id", "tectonic_region", "n_sources", "b_value", "mmin", "mmax",
                   "annual_rate_mw_ge_mmin", "rate_mw_ge_5_per_year",
                   "rate_mw_ge_7_per_year"]].to_string(index=False))
    print(f"Written to {out.relative_to(ROOT)} (workbook {WORKBOOK})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
