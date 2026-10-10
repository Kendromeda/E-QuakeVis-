"""Match the BMKG catalogue with the ISC Bulletin and fit Mw conversions.

Run after ``scripts/download_isc_bulletin.py``::

    python scripts/match_bmkg_isc.py

Steps:

1. BMKG events (``data/katalog_gempa_bmkg_2008_terbaru.csv``) are paired with
   ISC events, first through BMKG's own (DJA) origin in the ISC Bulletin
   (<= 5 s, <= 50 km), otherwise through the ISC prime origin (<= 16 s,
   <= 100 km); see ``utils.magnitude_homogenization.match_events``.
2. Each ISC event gets a reference Mw (GCMT > ISC-GEM from a moment > NEIC Mww >
   NEIC other Mw > GFZ > other agencies; proxies such as Mwp, ISC-GEM proxy Mw
   and BMKG's own Mw are not used). ISC-GEM (``data/psha/iscgem/``) is joined on
   the ISC event id when present.
3. For every matched event, the DJA magnitude in the ISC Bulletin with the same
   value as the BMKG magnitude reveals the real type of BMKG's untyped "M".
4. General Orthogonal Regression (eta = 1) of reference Mw on each BMKG
   magnitude type with enough pairs.

5. The same regression inside the ISC Bulletin itself (DJA, ISC mb, ISC MS and
   NEIC mb against the reference Mw of the same event), for every year.

Outputs in ``data/psha/isc/``: ``bmkg_isc_match.csv``,
``magnitude_conversion_summary.json``, ``magnitude_conversion.png`` and
``magnitude_conversion_isc.png`` (plots need matplotlib: run with ``.venv-oq``).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.iscgem import load_iscgem  # noqa: E402
from utils.magnitude_homogenization import (  # noqa: E402
    match_events,
    orthogonal_regression,
    reference_mw,
)

BMKG_PATH = ROOT / "data" / "katalog_gempa_bmkg_2008_terbaru.csv"
ISC_DIR = ROOT / "data" / "psha" / "isc"
ISCGEM_DIR = ROOT / "data" / "psha" / "iscgem"
MIN_PAIRS = 30
SAME_VALUE_TOLERANCE = 0.051  # ISC stores BMKG magnitudes rounded to 0.1
EXCLUDED_EVENT_TYPES = ("not existing", "other event", "volcanic eruption")
ISC_DJA_TYPES = ("MLv", "mb", "mB", "ML", "M", "Mw(mB)", "MW", "Mw", "Mwp", "MS", "Ms")


def load_bmkg() -> tuple[pd.DataFrame, int]:
    bmkg = pd.read_csv(BMKG_PATH, low_memory=False)
    excluded = bmkg["event_type"].isin(EXCLUDED_EVENT_TYPES)
    bmkg = bmkg.loc[~excluded].copy()
    bmkg["time"] = pd.to_datetime(bmkg["waktu_utc"], utc=True, format="ISO8601")
    bmkg = bmkg.rename(columns={"lintang": "lat", "bujur": "lon"})
    return bmkg, int(excluded.sum())


def load_isc() -> tuple[pd.DataFrame, pd.DataFrame]:
    events = pd.read_csv(ISC_DIR / "isc_events.csv", dtype={"isc_evid": str})
    for column in ("time", "dja_time"):
        events[column] = pd.to_datetime(events[column], utc=True, format="ISO8601")
    events = events.rename(columns={"latitude": "lat", "longitude": "lon",
                                    "dja_latitude": "dja_lat", "dja_longitude": "dja_lon"})
    mags = pd.read_csv(ISC_DIR / "isc_magnitudes.csv", dtype={"isc_evid": str})
    gem = iscgem_magnitudes()
    gem = gem.loc[gem["isc_evid"].isin(events["isc_evid"])]  # ISC-GEM is global
    return events, pd.concat([mags, gem], ignore_index=True)


def iscgem_magnitudes() -> pd.DataFrame:
    """ISC-GEM Mw as magnitude rows keyed by ISC event id (empty when absent).

    Mw computed from a moment keeps type "Mw" and can serve as a reference;
    proxy Mw (converted from MS/mb by ISC-GEM) is typed "Mw(proxy)".
    """
    frames = []
    for name, supplement in (("isc-gem-cat.csv", False), ("isc-gem-suppl.csv", True)):
        path = ISCGEM_DIR / name
        if path.is_file():
            frames.append(load_iscgem(path, supplement=supplement))
    if not frames:
        print("ISC-GEM tidak ditemukan di data/psha/iscgem/; dilewati.")
        return pd.DataFrame(columns=["isc_evid", "agency", "mag_type", "magnitude"])
    gem = pd.concat(frames, ignore_index=True).dropna(subset=["mw"])
    direct = (gem["mw_src"] == "d") & ~gem["supplement"]
    return pd.DataFrame({
        "isc_evid": gem["eventid"].astype(str),
        "agency": "ISC-GEM",
        "mag_type": direct.map({True: "Mw", False: "Mw(proxy)"}),
        "magnitude": gem["mw"],
        "uncertainty": gem["mw_unc"],
    })


def same_value_dja_type(matched: pd.DataFrame, mags: pd.DataFrame) -> pd.Series:
    """Type of the DJA magnitude in ISC whose value equals the BMKG magnitude."""
    dja = mags.loc[mags["agency"] == "DJA", ["isc_evid", "mag_type", "magnitude"]]
    joined = matched[["event_id", "isc_evid", "magnitudo"]].merge(dja, on="isc_evid")
    joined = joined.loc[(joined["magnitude"] - joined["magnitudo"]).abs()
                        <= SAME_VALUE_TOLERANCE]
    types = joined.groupby("event_id")["mag_type"].agg(lambda s: "/".join(sorted(set(s))))
    return matched["event_id"].map(types)


def _fit_entry(x: pd.Series, y: pd.Series, label: str) -> dict:
    entry: dict = {"pairs": int(len(x))}
    if len(x) >= MIN_PAIRS:
        fit = orthogonal_regression(x, y)
        entry.update({
            "equation": f"Mw = {fit.slope:.3f} * {label} + {fit.intercept:.3f}",
            "slope": round(fit.slope, 4), "intercept": round(fit.intercept, 4),
            "sigma": round(fit.sigma, 3),
            "valid_range": [round(fit.x_min, 2), round(fit.x_max, 2)],
            "mean_difference_mw_minus_m": round(float((y - x).mean()), 3),
        })
    return entry


def fit_conversions(table: pd.DataFrame) -> dict:
    """Reference Mw against the BMKG catalogue magnitude, per BMKG type."""
    pairs = table.dropna(subset=["mw_ref", "magnitudo"])
    return {str(mag_type): _fit_entry(group["magnitudo"], group["mw_ref"], str(mag_type))
            for mag_type, group in pairs.groupby("tipe_magnitudo")}


def isc_pairs(mags: pd.DataFrame, ref: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Reference Mw against other magnitudes reported in the same ISC event.

    Covers every year of the Bulletin, so it also calibrates magnitudes of
    events older than the BMKG catalogue (ISC mb, ISC MS) and BMKG's own (DJA)
    magnitudes as reported to ISC.
    """
    numeric = mags.assign(magnitude=pd.to_numeric(mags["magnitude"], errors="coerce"))
    numeric = numeric.dropna(subset=["magnitude"])
    selections = {f"DJA {t}": (numeric["agency"] == "DJA") & (numeric["mag_type"] == t)
                  for t in ISC_DJA_TYPES}
    selections.update({
        "ISC mb": (numeric["agency"] == "ISC") & (numeric["mag_type"] == "mb"),
        "ISC MS": (numeric["agency"] == "ISC") & (numeric["mag_type"].isin(["MS", "Ms"])),
        "NEIC mb": (numeric["agency"] == "NEIC") & (numeric["mag_type"] == "mb"),
    })
    result = {}
    for label, selected in selections.items():
        chosen = numeric.loc[selected].drop_duplicates("isc_evid")
        result[label] = chosen.merge(ref, on="isc_evid")[["isc_evid", "magnitude", "mw_ref"]]
    return result


def plot(panels: list[tuple[str, pd.Series, pd.Series, dict]], title: str, path: Path) -> None:
    try:
        import matplotlib
    except ImportError:  # The app environment has no matplotlib; .venv-oq does.
        print("matplotlib tidak tersedia; grafik dilewati (jalankan dengan .venv-oq).")
        return
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    panels = [panel for panel in panels if "slope" in panel[3]][:9]
    if not panels:
        return
    cols = min(3, len(panels))
    rows = int(np.ceil(len(panels) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.2 * cols, 4.0 * rows), squeeze=False)
    for ax, (label, x, y, fit) in zip(axes.flat, panels):
        ax.scatter(x, y, s=5, alpha=0.3, color="#2563eb", linewidths=0)
        lo, hi = 2.5, 9.5
        ax.plot([lo, hi], [lo, hi], color="#9ca3af", lw=1, ls="--", label="1:1")
        xs = np.linspace(*fit["valid_range"], 50)
        ax.plot(xs, fit["intercept"] + fit["slope"] * xs, color="#dc2626", lw=1.8,
                label=f"GOR: {fit['slope']:.2f}x + {fit['intercept']:.2f}")
        ax.set(xlim=(lo, hi), ylim=(lo, hi), xlabel=label,
               ylabel="Mw acuan (GCMT/NEIC/...)",
               title=f"{label} (n = {fit['pairs']}, σ = {fit['sigma']:.2f})")
        ax.legend(loc="upper left", fontsize=8, frameon=False)
        ax.grid(alpha=0.2)
    for ax in list(axes.flat)[len(panels):]:
        ax.set_visible(False)
    fig.suptitle(title, fontsize=12)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    bmkg, n_excluded = load_bmkg()
    events, mags = load_isc()
    pairs = match_events(bmkg[["event_id", "time", "lat", "lon"]], events)
    ref = reference_mw(mags)
    isc_mb = (mags.loc[(mags["agency"] == "ISC") & (mags["mag_type"] == "mb")]
              .drop_duplicates("isc_evid").set_index("isc_evid")["magnitude"])

    table = bmkg.merge(pairs, on="event_id", how="left").merge(ref, on="isc_evid", how="left")
    table["isc_mb"] = table["isc_evid"].map(isc_mb)
    matched = table["isc_evid"].notna()
    table.loc[matched, "dja_type_same_value"] = same_value_dja_type(table.loc[matched], mags)
    columns = ["event_id", "waktu_utc", "lat", "lon", "kedalaman", "magnitudo",
               "tipe_magnitudo", "isc_evid", "matched_on", "dt_s", "dist_km", "mw_ref",
               "mw_ref_agency", "mw_ref_type", "isc_mb", "dja_type_same_value"]
    table = table[columns].rename(columns={"lat": "lintang", "lon": "bujur"})
    table.to_csv(ISC_DIR / "bmkg_isc_match.csv", index=False, float_format="%.4f")

    by_type = table.groupby("tipe_magnitudo", dropna=False).agg(
        events=("event_id", "size"),
        matched=("isc_evid", lambda s: int(s.notna().sum())),
        with_mw_ref=("mw_ref", lambda s: int(s.notna().sum())),
    )
    untyped = table.loc[(table["tipe_magnitudo"] == "M") & table["dja_type_same_value"].notna()]
    fits = fit_conversions(table)
    internal = isc_pairs(mags, ref)
    internal_fits = {label: _fit_entry(frame["magnitude"], frame["mw_ref"], label)
                     for label, frame in internal.items()}
    years = events["time"].dt.year
    summary = {
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bmkg_events": int(len(table)),
        "bmkg_excluded_non_earthquake": n_excluded,
        "isc_events": int(len(events)),
        "isc_years": [int(years.min()), int(years.max())],
        "isc_events_with_reference_mw": int(len(ref)),
        "isc_events_reference_mw_by_agency": ref["mw_ref_agency"].value_counts().to_dict(),
        "iscgem_rows": mags.loc[mags["agency"] == "ISC-GEM", "mag_type"]
            .value_counts().to_dict(),
        "matched": int(matched.sum()),
        "matched_on": table["matched_on"].value_counts().to_dict(),
        "with_reference_mw": int(table["mw_ref"].notna().sum()),
        "reference_mw_agency": table["mw_ref_agency"].value_counts().to_dict(),
        "by_bmkg_type": {str(k): {c: int(v) for c, v in row.items()}
                         for k, row in by_type.iterrows()},
        "untyped_M_resolved_by_dja_value": untyped["dja_type_same_value"]
            .value_counts().head(10).to_dict(),
        "conversions_gor_eta_1": fits,
        "isc_internal_conversions_gor_eta_1": internal_fits,
        "notes": [
            "isc_internal_* pairs magnitudes reported within the same ISC event and "
            "covers every year of the Bulletin (also before the BMKG catalogue).",
            "Conversions are valid only inside valid_range; do not extrapolate.",
            "eta = 1 assumes equal error variance in both magnitudes.",
        ],
    }
    (ISC_DIR / "magnitude_conversion_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with_mw = table.dropna(subset=["mw_ref"])
    plot([(f"BMKG {t}", g["magnitudo"], g["mw_ref"], fits[str(t)])
          for t, g in with_mw.groupby("tipe_magnitudo")],
         "Konversi magnitudo katalog BMKG ke Mw (pasangan BMKG–ISC)",
         ISC_DIR / "magnitude_conversion.png")
    plot([(label, frame["magnitude"], frame["mw_ref"], internal_fits[label])
          for label, frame in internal.items()],
         "Konversi magnitudo ke Mw di dalam buletin ISC (semua tahun)",
         ISC_DIR / "magnitude_conversion_isc.png")
    print(json.dumps({k: summary[k] for k in ("bmkg_events", "matched", "with_reference_mw")},
                     indent=1))
    for heading, group in (("BMKG katalog", fits), ("Di dalam ISC", internal_fits)):
        print(heading)
        for label, fit in group.items():
            print(f"{label:>12}: {fit['pairs']:>6} pasangan  {fit.get('equation', '')}"
                  f"{'  sigma=%.2f' % fit['sigma'] if 'sigma' in fit else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
