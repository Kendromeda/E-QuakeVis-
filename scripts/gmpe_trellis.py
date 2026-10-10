"""Median ground motion and sigma of GMPEs versus distance (trellis data).

Runs in the OpenQuake environment; the app calls it as a subprocess::

    .venv-oq\\Scripts\\python.exe scripts\\gmpe_trellis.py < request.json

Request (JSON on stdin)::

    {"gmpes": ["BooreEtAl2014", "AbrahamsonGulerce2020SInterCascadia"],
     "trt": "Subduction Interface", "imt": "PGA", "mags": [7.0, 8.0],
     "distances": [10, 20, 50, 100, 200], "vs30": 760}

Response (JSON on stdout): for every GMPE and magnitude, the median (g) and
total sigma (natural log units) at each distance, or an error message.

The rupture is a simplified generic one per tectonic region type (dip, rake,
top depth, hypocentre depth and a magnitude-dependent width); distances are
Joyner-Boore distances on the hanging-wall side, with rrup derived from them.
Basin depths follow the CY14/CB14 Vs30 relations. This is meant for comparing
GMPEs, not for site-specific ground-motion estimates.
"""
from __future__ import annotations

import json
import math
import sys
import warnings

import numpy as np

# Generic rupture per tectonic region type: dip, rake, top of rupture (km),
# hypocentre depth (km), maximum down-dip width (km).
GEOMETRY = {
    "Active Shallow Crust": dict(dip=60.0, rake=90.0, ztor=0.0, hypo_depth=10.0,
                                 max_width=20.0),
    "Subduction Interface": dict(dip=15.0, rake=90.0, ztor=10.0, hypo_depth=20.0,
                                 max_width=200.0),
    "Subduction Intraslab": dict(dip=45.0, rake=-90.0, ztor=60.0, hypo_depth=70.0,
                                 max_width=40.0),
}
GEOMETRY["Subduction Inslab"] = GEOMETRY["Subduction Intraslab"]


def rupture_width(mag: float, max_width: float) -> float:
    """Down-dip width (km), Wells & Coppersmith (1994) all slip types, capped."""
    return min(10 ** (-1.01 + 0.32 * mag), max_width)


def basin_depths(vs30: float) -> tuple[float, float]:
    """z1.0 (m, Chiou & Youngs 2014) and z2.5 (km, Campbell & Bozorgnia 2014)."""
    z1pt0 = math.exp(-7.15 / 4 * math.log((vs30 ** 4 + 571 ** 4) / (1360 ** 4 + 571 ** 4)))
    z2pt5 = math.exp(7.089 - 1.144 * math.log(vs30))
    return z1pt0, z2pt5


def compute(request: dict) -> dict:
    from openquake.hazardlib import valid
    from openquake.hazardlib.contexts import simple_cmaker

    trt = request["trt"]
    geometry = {**GEOMETRY.get(trt, GEOMETRY["Active Shallow Crust"]),
                **request.get("geometry", {})}
    distances = np.asarray(request["distances"], dtype=float)
    vs30 = float(request.get("vs30", 760.0))
    z1pt0, z2pt5 = basin_depths(vs30)
    imt = request["imt"]
    results = []
    for spec in request["gmpes"]:
        entry: dict = {"gmpe": spec, "curves": []}
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                gsim = valid.gsim(spec)
                for mag in request["mags"]:
                    mag = float(mag)
                    cmaker = simple_cmaker([gsim], [imt], mags=[f"{mag:.2f}"])
                    ctx = cmaker.new_ctx(len(distances))
                    dip = math.radians(geometry["dip"])
                    width = rupture_width(mag, geometry["max_width"])
                    ztor = geometry["ztor"]
                    ctx.mag = mag
                    ctx.rake = geometry["rake"]
                    ctx.dip = geometry["dip"]
                    ctx.ztor = ztor
                    ctx.hypo_depth = max(geometry["hypo_depth"], ztor)
                    ctx.width = width
                    ctx.rjb = distances
                    # Hanging-wall side: the rupture projects width*cos(dip) km.
                    ctx.rx = distances + width * math.cos(dip)
                    ctx.ry0 = 0.0
                    ctx.rrup = np.sqrt(distances ** 2 + ztor ** 2)
                    ctx.rhypo = np.sqrt(distances ** 2 + ctx.hypo_depth ** 2)
                    ctx.repi = distances
                    ctx.vs30 = vs30
                    ctx.vs30measured = True
                    ctx.z1pt0 = z1pt0
                    ctx.z2pt5 = z2pt5
                    ctx.backarc = False
                    mean, sigma, _tau, _phi = cmaker.get_mean_stds([ctx])
                    entry["curves"].append({
                        "mag": mag,
                        "median_g": np.exp(mean[0, 0]).round(6).tolist(),
                        "sigma_ln": sigma[0, 0].round(4).tolist(),
                    })
        except Exception as exc:  # Reported per GMPE; other GMPEs still run.
            entry["error"] = f"{type(exc).__name__}: {exc}"
        results.append(entry)
    return {"trt": trt, "imt": imt, "vs30": vs30, "distances": distances.tolist(),
            "geometry": geometry, "results": results}


def main() -> int:
    request = json.load(sys.stdin)
    json.dump(compute(request), sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
