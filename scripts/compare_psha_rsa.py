"""Compare a PSHA run with SNI 1726:2019 values from RSA Cipta Karya.

RSA (https://rsa.ciptakarya.pu.go.id/2021/) returns, per coordinate, the SNI
1726:2019 map values derived from PuSGeN 2017 on site class SB:

- PGA: MCE_G, geometric mean, 2% in 50 years -> comparable with ``PGA-0.02``
- Ss, S1: MCE_R, risk-targeted and maximum-direction -> NOT directly comparable
  with uniform-hazard SA(0.2)/SA(1.0); stored for reference only

Usage (app environment)::

    python scripts/compare_psha_rsa.py --run-id jabar_0p1 --step 0.3

Sites on a ``--step`` degree lattice (plus the control cities) are queried one
by one with a pause between requests. Results are written to
``data/psha/results/<run_id>/validation_rsa.csv`` and summarised in
``validation_rsa.json``.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.cookiejar
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.psha_results import list_psha_runs, load_hazard_map, nearest_site  # noqa: E402

RSA_URL = "https://rsa.ciptakarya.pu.go.id/2021/"
CONTROL_CITIES = {
    "Jakarta": (106.83, -6.18), "Bandung": (107.61, -6.91), "Bogor": (106.80, -6.60),
    "Sukabumi": (106.93, -6.92), "Serang": (106.15, -6.12), "Pelabuhan Ratu": (106.55, -6.99),
    "Cirebon": (108.55, -6.72), "Tasikmalaya": (108.22, -7.33), "Padang": (100.36, -0.95),
    "Yogyakarta": (110.37, -7.80), "Surabaya": (112.75, -7.25), "Palu": (119.87, -0.90),
    "Bengkulu": (102.26, -3.80), "Bandar Lampung": (105.26, -5.43),
}


class RSAClient:
    """Session-bound client for the RSA coordinate form (CSRF token + cookie)."""

    def __init__(self, pause_seconds: float = 1.5) -> None:
        self.opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
        page = self.opener.open(RSA_URL, timeout=60).read().decode("utf-8", "replace")
        match = re.search(r'name="csrf-token" content="([^"]+)"', page)
        if not match:
            raise RuntimeError("RSA page has no CSRF token; the site may have changed.")
        self.token = match.group(1)
        self.pause_seconds = pause_seconds

    def query(self, lon: float, lat: float) -> dict[str, float]:
        request = Request(
            RSA_URL + "process.php",
            data=urlencode({"bujur": f"{lon:.2f}", "lintang": f"{lat:.2f}"}).encode(),
            headers={"X-CSRF-TOKEN": self.token, "X-Requested-With": "XMLHttpRequest"},
        )
        payload = json.loads(self.opener.open(request, timeout=60).read().decode("utf-8"))
        time.sleep(self.pause_seconds)
        return {key: float(payload[key]) for key in ("pga", "ss", "s1")}


def select_sites(hazard: pd.DataFrame, step: float) -> pd.DataFrame:
    """Sites on a coarser lattice plus the nearest site of each control city."""
    def on_lattice(values: pd.Series) -> np.ndarray:
        ratio = values.to_numpy(float) / step
        return np.abs(ratio - np.rint(ratio)) < 1e-6

    mask = on_lattice(hazard["lon"]) & on_lattice(hazard["lat"])
    chosen = {int(i): "" for i in np.flatnonzero(mask)}
    for name, (lon, lat) in CONTROL_CITIES.items():
        index, distance = nearest_site(hazard, lon, lat)
        if distance <= 10:
            chosen[index] = name
    sites = hazard.iloc[sorted(chosen)].copy()
    sites.insert(0, "lokasi", [chosen[i] for i in sorted(chosen)])
    return sites


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--step", type=float, default=0.3, help="sampling lattice (degrees)")
    parser.add_argument("--pause", type=float, default=1.5, help="seconds between requests")
    args = parser.parse_args()

    run = next((r for r in list_psha_runs(ROOT / "data" / "psha" / "results")
                if r.run_id == args.run_id), None)
    if run is None:
        raise SystemExit(f"Run {args.run_id} not found.")
    sites = select_sites(load_hazard_map(run), args.step)
    client = RSAClient(args.pause)
    rows = []
    for _, site in sites.iterrows():
        rsa = client.query(site["lon"], site["lat"])
        rows.append({
            "lokasi": site["lokasi"], "lon": site["lon"], "lat": site["lat"],
            "pga_psha_2475_g": round(site["PGA-0.02"], 4), "pga_rsa_sni_g": rsa["pga"],
            "rasio_pga": round(site["PGA-0.02"] / rsa["pga"], 3),
            "sa02_psha_2475_g": round(site.get("SA(0.2)-0.02", np.nan), 4), "ss_rsa_sni_g": rsa["ss"],
            "sa10_psha_2475_g": round(site.get("SA(1.0)-0.02", np.nan), 4), "s1_rsa_sni_g": rsa["s1"],
        })
        print(f"{site['lat']:6.2f} {site['lon']:7.2f}  PSHA {site['PGA-0.02']:.3f}  RSA {rsa['pga']:.3f}")
    table = pd.DataFrame(rows)
    table.to_csv(run.path / "validation_rsa.csv", index=False)
    ratio = table["rasio_pga"]
    summary = {
        "reference": "SNI 1726:2019 via RSA Cipta Karya (PuSGeN 2017), PGA = MCE_G on SB",
        "reference_url": RSA_URL,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "compared": "PGA 2% in 50 years (mean, Vs30 760 m/s) / RSA PGA",
        "num_sites": len(table),
        "ratio_median": round(float(ratio.median()), 3),
        "ratio_p10": round(float(ratio.quantile(0.1)), 3),
        "ratio_p90": round(float(ratio.quantile(0.9)), 3),
        "ratio_min": round(float(ratio.min()), 3),
        "ratio_max": round(float(ratio.max()), 3),
        "within_20_percent": int(((ratio - 1).abs() <= 0.2).sum()),
    }
    (run.path / "validation_rsa.json").write_text(json.dumps(summary, indent=2) + "\n",
                                                   encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
