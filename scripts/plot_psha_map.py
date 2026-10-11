r"""Export a static hazard map (PNG) from a finished ``scripts/run_psha.py`` run.

The app draws its own interactive map; this is for figures that go into reports
and papers, where a file beats a screenshot. Classes and colours are the ones in
``utils/psha_results.py``, so a figure is comparable with the app and with other
runs.

    python scripts\plot_psha_map.py manggarai_barat_gmpe_pilihan_pengguna_run1 \
        --areas "Manggarai Barat"

Without ``--output`` the figure lands in the run folder as ``peta.png``.
Needs matplotlib (in requirements.txt); the app itself does not.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.psha_results import (  # noqa: E402
    HAZARD_BREAKS_G,
    HAZARD_COLORS,
    hazard_grid,
    hazard_legend_labels,
    list_psha_runs,
    load_hazard_map,
)
from utils.psha_sites import KABUPATEN_KEY, load_kabupaten_features  # noqa: E402

RESULTS_DIR = ROOT / "data" / "psha" / "results"
# Return periods are labelled from the probability of exceedance in 50 years.
POE_LABELS = {"0.1": "10% / 50 thn (ulang 475 thn)",
              "0.02": "2% / 50 thn (ulang 2475 thn)"}


def outlines(area_names: list[str]) -> list[np.ndarray]:
    """Exterior rings of the named kabupaten/kota, for the boundary overlay."""
    wanted = {name.casefold() for name in area_names}
    rings = []
    for feature in load_kabupaten_features():
        if str(feature["properties"].get(KABUPATEN_KEY, "")).casefold() not in wanted:
            continue
        geometry = feature["geometry"]
        polygons = ([geometry["coordinates"]] if geometry["type"] == "Polygon"
                    else geometry["coordinates"])
        rings.extend(np.asarray(polygon[0], dtype=float) for polygon in polygons)
    return rings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_id", help="folder name under data/psha/results")
    parser.add_argument("--imt", default="PGA", choices=sorted(HAZARD_BREAKS_G),
                        help="intensity measure to map")
    parser.add_argument("--areas", nargs="*", default=[],
                        help="kabupaten/kota to outline on the map")
    parser.add_argument("--output", type=Path,
                        help="PNG to write (default: <run>/peta.png)")
    parser.add_argument("--dpi", type=int, default=135)
    args = parser.parse_args()

    try:
        import matplotlib
    except ImportError:
        raise SystemExit("Butuh matplotlib: pip install -r requirements.txt")
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, ListedColormap
    from matplotlib.patches import Patch

    runs = {run.run_id: run for run in list_psha_runs(RESULTS_DIR)}
    if args.run_id not in runs:
        raise SystemExit(f"Run {args.run_id} belum selesai atau tidak ada "
                         f"manifest.json. Yang tersedia: {', '.join(sorted(runs))}")
    run = runs[args.run_id]
    frame = load_hazard_map(run)
    output = args.output or run.path / "peta.png"

    breaks = HAZARD_BREAKS_G[args.imt]
    colormap = ListedColormap(HAZARD_COLORS)
    norm = BoundaryNorm(range(len(HAZARD_COLORS) + 1), colormap.N)
    rings = outlines(args.areas)
    poes = [f"{poe:g}" for poe in run.manifest["poes"]]

    figure, axes = plt.subplots(1, len(poes), figsize=(6.8 * len(poes), 6.2),
                                constrained_layout=True, squeeze=False)
    for axis, poe in zip(axes[0], poes):
        values, (west, south, east, north) = hazard_grid(
            frame, f"{args.imt}-{poe}", run.spacing)
        classes = np.where(
            np.isfinite(values),
            np.searchsorted(np.asarray(breaks[1:]), values, side="right"), np.nan)
        axis.imshow(classes, extent=(west, east, south, north), origin="upper",
                    cmap=colormap, norm=norm, interpolation="nearest")
        for ring in rings:
            axis.plot(ring[:, 0], ring[:, 1], color="#111111", linewidth=0.9)
        axis.set_xlim(west, east)
        axis.set_ylim(south, north)
        axis.set_title(f"{args.imt}, {POE_LABELS.get(poe, f'PoE {poe}')}", fontsize=11)
        axis.set_xlabel("Bujur (derajat)")
        axis.set_ylabel("Lintang (derajat)")

    figure.legend(
        handles=[Patch(facecolor=colour, edgecolor="none", label=label)
                 for colour, label in zip(HAZARD_COLORS, hazard_legend_labels(breaks))],
        loc="outside right center", title=f"{args.imt} (g)", fontsize=8,
        title_fontsize=9, frameon=False)
    region = ", ".join(args.areas) if args.areas else run.run_id
    figure.suptitle(
        f"Peta bahaya gempa (PSHA) - {region}\n"
        f"{run.manifest['num_sites']} situs, grid {run.spacing:g} derajat, "
        f"Vs30 {run.manifest['site_condition']['vs30_m_s']:g} m/s, "
        f"GEM IDN {run.manifest['model']['model_version']}", fontsize=12)
    figure.savefig(output, dpi=args.dpi, bbox_inches="tight")
    print(f"{output.relative_to(ROOT) if output.is_relative_to(ROOT) else output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
