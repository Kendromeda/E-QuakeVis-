"""Run a classical PSHA with a GEM IDN model and store app-ready results.

Needs the OpenQuake environment (Python >= 3.11), not the app environment::

    .venv-oq\\Scripts\\python.exe scripts\\run_psha.py --run-id jabar_0p1_v2024 \\
        --provinces "Jawa Barat" "Banten" "DKI Jakarta"
    .venv-oq\\Scripts\\python.exe scripts\\run_psha.py --run-id jabar_0p1 \\
        --model v2017.1.0 --provinces "Jawa Barat" "Banten" "DKI Jakarta"

Sites are land points of a regular lon/lat grid inside the selected provinces
(``data/provinsi_indonesia_38.geojson``). The job reuses the GEM model files in
place (``scripts/download_gem_idn_model.py``) with GEM's own source, GMPE and
distance settings; only the site set, IMTs, return periods and reference Vs30
are chosen here. Results go to ``data/psha/results/<run_id>/``:

- ``hazard_map.csv``: lon, lat and one column per ``<IMT>-<poe>`` (g)
- ``hazard_curves-<IMT>.csv``: lon, lat and one column per intensity level
- ``sites.csv``, ``job.ini`` and ``manifest.json`` describing the run

Set ``OQ_DATADIR`` to keep OpenQuake's datastores off the system drive.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.gmpe_catalog import write_gmm_logic_tree  # noqa: E402
from utils.psha_sites import (  # noqa: E402
    KABUPATEN_KEY,
    build_sites,
    load_kabupaten_features,
    load_province_features,
)

PSHA_DIR = ROOT / "data" / "psha"
RESULTS_DIR = PSHA_DIR / "results"

INVESTIGATION_TIME = 50.0
# 10 % and 2 % probability of exceedance in 50 years (475 and 2475 years).
POES = (0.1, 0.02)
# Intensity level ranges follow the GEM IDN job.ini.
IMT_LEVELS = {
    "PGA": (0.005, 3.0),
    "SA(0.2)": (0.005, 9.0),
    "SA(1.0)": (0.005, 3.6),
}
NUM_LEVELS = 25
POINTSOURCE_DISTANCE_KM = 200


@dataclass(frozen=True)
class GemModel:
    """The parts of GEM's job.ini that the E-QuakeVis job keeps unchanged."""

    source_logic_tree: str
    # Tectonic region type -> reqv lookup file (names differ between versions).
    reqv: dict[str, str]
    maximum_distance_km: dict[str, float]
    complex_fault_mesh_spacing: float
    # None lets the GMPEs derive basin depths from Vs30 (GEM's -999).
    z1pt0_m: float | None
    z2pt5_km: float | None
    use_rates: bool

    def directory(self, version: str) -> Path:
        return PSHA_DIR / f"gem_idn_{version}"


MODELS = {
    "v2024.0.0": GemModel(
        source_logic_tree="ssmLT_clean.xml",
        reqv={"Active Shallow Crust": "lookup_reqv_asc.hdf5",
              "Subduction Intraslab": "lookup_reqv_slb.hdf5"},
        maximum_distance_km={"Subduction Interface": 1000.0, "default": 300.0},
        complex_fault_mesh_spacing=20.0,
        z1pt0_m=None,
        z2pt5_km=None,
        use_rates=True,
    ),
    "v2017.1.0": GemModel(
        source_logic_tree="ssmLT.xml",
        reqv={"Active Shallow Crust": "lookup_reqv_asc.hdf5",
              "Subduction Inslab": "lookup_reqv_slb.hdf5"},
        maximum_distance_km={"default": 300.0},
        complex_fault_mesh_spacing=10.0,
        z1pt0_m=30.0,
        z2pt5_km=0.57,
        use_rates=False,
    ),
}
DEFAULT_MODEL = "v2024.0.0"


def return_period(poe: float, investigation_time: float = INVESTIGATION_TIME) -> float:
    """Poissonian return period of a probability of exceedance."""
    return -investigation_time / math.log1p(-poe)


def clear_stale_jobs() -> None:
    """Mark OpenQuake jobs whose process died as failed.

    A killed run stays "executing" in the OpenQuake database and the next run
    then waits for it forever ("Waiting for jobs N").
    """
    import psutil
    import socket
    from openquake.commonlib import logs

    try:
        jobs = logs.dbcmd(
            "SELECT id, pid FROM job WHERE status IN ('executing', 'submitted') "
            "AND host=?x AND pid > 0", socket.gethostname())
    except Exception as exc:  # The run itself reports database problems.
        print(f"Could not check stale OpenQuake jobs: {exc}")
        return
    for job in jobs:
        if not psutil.pid_exists(job.pid):
            logs.dbcmd("set_status", job.id, "failed")
            print(f"Marked stale OpenQuake job {job.id} as failed")


def _depth(value: float | None) -> str:
    return "-999" if value is None else f"{value:g}"


def write_job(run_dir: Path, args: argparse.Namespace) -> None:
    model = MODELS[args.model]
    model_in = os.path.relpath(model.directory(args.model) / "in", run_dir).replace("\\", "/")
    ps_grid = (f"ps_grid_spacing = {args.ps_grid_spacing:g}\n"
               if args.ps_grid_spacing else "")
    use_rates = "use_rates = true\n" if model.use_rates else ""
    levels = ",\n    ".join(
        f'"{imt}": logscale({low}, {high}, {NUM_LEVELS})'
        for imt, (low, high) in IMT_LEVELS.items() if imt in args.imts)
    # A user logic tree (``--gmm-lt-json``) is written next to job.ini.
    gmm_lt = "gmmLT.xml" if args.gmm_lt_json else f"{model_in}/gmmLT.xml"
    distances = ", ".join(f"'{trt}': {km:g}" for trt, km in model.maximum_distance_km.items())
    reqv = ",\n  ".join(f'"{trt}": "{model_in}/{name}"' for trt, name in model.reqv.items())
    # A Tahap B source model (scripts/build_source_model.py) replaces GEM's sources;
    # GEM's rupture settings (reqv tables, mesh spacing, distances) stay.
    if getattr(args, "source_model", None):
        ssm_lt = os.path.relpath(args.source_model / "ssmLT.xml", run_dir).replace("\\", "/")
        described = f"model sumber {args.source_model.name}"
    else:
        ssm_lt = f"{model_in}/{model.source_logic_tree}"
        described = f"GEM IDN {args.model}"
    job = f"""[general]
description = E-QuakeVis PSHA {args.run_id} ({described})
calculation_mode = classical
random_seed = 23

[geometry]
sites_csv = sites.csv

[logic_tree]
number_of_logic_tree_samples = 0

[erf]
rupture_mesh_spacing = 5
complex_fault_mesh_spacing = {model.complex_fault_mesh_spacing:g}
width_of_mfd_bin = 0.1
area_source_discretization = 10.0

[site_params]
reference_vs30_type = measured
reference_vs30_value = {args.vs30}
reference_depth_to_1pt0km_per_sec = {_depth(model.z1pt0_m)}
reference_depth_to_2pt5km_per_sec = {_depth(model.z2pt5_km)}

[calculation]
source_model_logic_tree_file = {ssm_lt}
gsim_logic_tree_file = {gmm_lt}
investigation_time = {INVESTIGATION_TIME}
intensity_measure_types_and_levels = {{
    {levels}}}
truncation_level = 5.0
maximum_distance = {{{distances}}}
horiz_comp_to_geom_mean = true
pointsource_distance = {POINTSOURCE_DISTANCE_KM}
{ps_grid}{use_rates}reqv_file = {{
  {reqv}}}

[output]
export_dir = export
mean_hazard_curves = true
quantile_hazard_curves =
hazard_maps = true
uniform_hazard_spectra = false
poes = {' '.join(str(p) for p in POES)}
"""
    (run_dir / "job.ini").write_text(job, encoding="utf-8")


def _read_oq_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    """OpenQuake CSV exports start with a '#' metadata line."""
    with path.open(encoding="utf-8") as handle:
        rows = [row for row in csv.reader(handle) if row and not row[0].startswith("#")]
    return rows[0], rows[1:]


def collect_outputs(run_dir: Path, imts: list[str]) -> dict[str, str]:
    """Copy the mean map/curves to stable names; return {file: sha256}."""
    export = run_dir / "export"
    outputs = {}
    maps = sorted(export.glob("hazard_map-mean_*.csv"))
    if len(maps) != 1:
        raise RuntimeError(f"Expected one mean hazard map export, found {len(maps)}")
    targets = [(maps[0], "hazard_map.csv")]
    for imt in imts:
        curves = sorted(export.glob(f"hazard_curve-mean-{imt}_*.csv"))
        if len(curves) != 1:
            raise RuntimeError(f"Expected one mean hazard curve export for {imt}")
        targets.append((curves[0], f"hazard_curves-{imt}.csv"))
    for source, name in targets:
        header, rows = _read_oq_csv(source)
        drop = {i for i, column in enumerate(header) if column == "depth"}
        with (run_dir / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow([c for i, c in enumerate(header) if i not in drop])
            writer.writerows([[c for i, c in enumerate(row) if i not in drop] for row in rows])
        outputs[name] = hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True, help="folder name under data/psha/results")
    parser.add_argument("--model", choices=list(MODELS), default=DEFAULT_MODEL,
                        help="GEM IDN model version (download it first)")
    parser.add_argument("--provinces", nargs="*", help="limit sites to these provinces")
    parser.add_argument("--kabupaten", nargs="*",
                        help="limit sites to these kabupaten/kota (overrides --provinces)")
    parser.add_argument("--spacing", type=float, default=0.1, help="site spacing in degrees")
    parser.add_argument("--vs30", type=float, default=760.0, help="reference Vs30 (m/s)")
    parser.add_argument("--vs30-source", choices=["reference", "usgs"], default="reference",
                        help="'usgs': Vs30 per site from the USGS global proxy grid "
                             "(scripts/prepare_usgs_vs30.py) instead of one reference value")
    parser.add_argument(
        "--ps-grid-spacing", type=float, default=0.0,
        help="km; OpenQuake grids distant point sources (0 = exact, slower)")
    parser.add_argument("--imts", nargs="+", default=list(IMT_LEVELS), choices=list(IMT_LEVELS))
    parser.add_argument(
        "--gmm-lt-json", type=Path,
        help="GMPE logic tree chosen by the user: {trt: [{gmpe, weight}]} (JSON); "
             "default is the model's own gmmLT.xml")
    parser.add_argument(
        "--source-model", type=Path,
        help="Tahap B source model folder (scripts/build_source_model.py) instead of GEM's "
             "sources; its site box is used unless --provinces/--kabupaten/--bounds is given")
    parser.add_argument("--bounds", nargs=4, type=float,
                        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                        help="limit sites to land points in this box")
    parser.add_argument("--dry-run", action="store_true", help="write sites and job.ini only")
    args = parser.parse_args()
    source_manifest = None
    if args.source_model:
        args.source_model = args.source_model.resolve()
        manifest_path = args.source_model / "manifest.json"
        if not (args.source_model / "ssmLT.xml").is_file() or not manifest_path.is_file():
            raise SystemExit(f"{args.source_model} is not a finished source model "
                             "(no ssmLT.xml/manifest.json)")
        source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not (args.bounds or args.provinces or args.kabupaten):
            args.bounds = source_manifest["site_bounds"]

    model = MODELS[args.model]
    model_dir = model.directory(args.model)
    if not (model_dir / "manifest.json").is_file():
        raise SystemExit(f"Model {args.model} not found in {model_dir.relative_to(ROOT)}; run "
                         f"scripts/download_gem_idn_model.py --version {args.model}")

    run_dir = RESULTS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        box = tuple(args.bounds) if args.bounds else None
        if args.kabupaten:
            sites = build_sites(load_kabupaten_features(), args.kabupaten, args.spacing,
                                key=KABUPATEN_KEY, bounds=box)
        else:
            sites = build_sites(load_province_features(), args.provinces, args.spacing,
                                bounds=box)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not sites:
        raise SystemExit("No land sites in the selected area.")
    vs30_values = None
    if args.vs30_source == "usgs":
        # A site model (sites.csv with vs30 columns, like GEM's iid-10km.csv):
        # each site gets the USGS proxy Vs30; basin depths follow from Vs30.
        from utils.vs30 import Vs30Error, load_grid, sample

        try:
            vs30_values = sample([s[0] for s in sites], [s[1] for s in sites], load_grid())
        except Vs30Error as exc:
            raise SystemExit(str(exc)) from exc
    with (run_dir / "sites.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if vs30_values is None:
            writer.writerows(sites)
        else:
            writer.writerow(["lon", "lat", "vs30", "z1pt0", "z2pt5", "vs30measured"])
            writer.writerows([(lon, lat, f"{vs30:g}", -999, -999, 0)
                              for (lon, lat), vs30 in zip(sites, vs30_values)])
    gmm_logic_tree = None
    if args.gmm_lt_json:
        gmm_logic_tree = json.loads(args.gmm_lt_json.read_text(encoding="utf-8"))
        write_gmm_logic_tree(gmm_logic_tree, run_dir / "gmmLT.xml")
    write_job(run_dir, args)
    print(f"{len(sites)} sites, GEM IDN {args.model}, job written to "
          f"{run_dir.relative_to(ROOT)}")
    if args.dry_run:
        return 0

    shutil.rmtree(run_dir / "export", ignore_errors=True)
    clear_stale_jobs()
    started = time.time()
    # SSLKEYLOGFILE (set by some antivirus software) crashes OpenSSL inside
    # OpenQuake on Windows ("no OPENSSL_Applink"); OpenQuake does not need it.
    oq_env = {key: value for key, value in os.environ.items() if key != "SSLKEYLOGFILE"}
    # scripts/oq_run.py is "python -m openquake.commands" with E-QuakeVis'
    # start-up memory threshold (utils.psha_jobs.OQ_MEMORY_LIMIT_PERCENT).
    subprocess.run([sys.executable, str(ROOT / "scripts" / "oq_run.py"),
                    "run", "job.ini", "-e", "csv"],
                   cwd=run_dir, check=True, env=oq_env)
    elapsed = time.time() - started
    outputs = collect_outputs(run_dir, args.imts)
    shutil.rmtree(run_dir / "export")

    from openquake.baselib import __version__ as oq_version
    model_info = json.loads((model_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "run_id": args.run_id,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "openquake_version": oq_version,
        "model": {key: model_info[key] for key in
                  ("name", "model_version", "authors", "license", "source", "documentation")},
        "region": {"provinces": args.provinces or "all", "spacing_degrees": args.spacing,
                   **({"kabupaten": args.kabupaten} if args.kabupaten else {}),
                   **({"bounds": list(args.bounds)} if args.bounds else {})},
        **({"source_model": {
            "name": args.source_model.name,
            "path": os.path.relpath(args.source_model, ROOT).replace("\\", "/"),
            "created_utc": source_manifest["created_utc"],
            "description": "Tahap B: gempa latar dari katalog sendiri; sesar dan megathrust "
                           "PuSGeN 2024 via GEM IDN v2024",
        }} if args.source_model else {}),
        "num_sites": len(sites),
        "approximations": {"ps_grid_spacing_km": args.ps_grid_spacing,
                           "pointsource_distance_km": POINTSOURCE_DISTANCE_KM,
                           "maximum_distance_km": model.maximum_distance_km},
        "site_condition": ({"vs30_m_s": args.vs30, "vs30_type": "measured",
                            "z1pt0_m": model.z1pt0_m, "z2pt5_km": model.z2pt5_km}
                           if vs30_values is None else
                           {"vs30_m_s": float(np.median(vs30_values)), "vs30_source": "usgs",
                            "vs30_type": "inferred (topographic-slope proxy)",
                            "vs30_min_m_s": float(vs30_values.min()),
                            "vs30_max_m_s": float(vs30_values.max()),
                            "z1pt0_m": None, "z2pt5_km": None,
                            "citation": "Heath et al. (2020), Earthquake Spectra 36(3)",
                            "note": "vs30_m_s is the median; per-site values are in sites.csv"}),
        "investigation_time_years": INVESTIGATION_TIME,
        "poes": list(POES),
        "return_periods_years": [round(return_period(p)) for p in POES],
        "imts": args.imts,
        "gmpe_logic_tree": {
            "source": "user" if gmm_logic_tree else f"GEM IDN {args.model}",
            "branches": gmm_logic_tree,
        },
        "statistic": "mean over the full source and GMPE logic tree",
        "units": "g",
        "runtime_seconds": round(elapsed),
        "outputs": outputs,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Finished in {elapsed / 60:.1f} min: {', '.join(outputs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
