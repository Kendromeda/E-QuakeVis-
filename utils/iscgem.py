"""Read the ISC-GEM Global Instrumental Earthquake Catalogue (CSV release).

The catalogue (``isc-gem-cat.csv``) and its supplement (``isc-gem-suppl.csv``)
are requested from http://www.isc.ac.uk/iscgem/ and stored in
``data/psha/iscgem/``. Both files share one format: ``#`` comment lines, then
comma-separated, space-padded columns. Column names repeat (``q``, ``unc``), so
they are renamed here.

International Seismological Centre (2025), ISC-GEM Earthquake Catalogue,
https://doi.org/10.31905/D808B825 (CC BY-SA 3.0).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

COLUMNS = [
    "date", "lat", "lon", "smajax", "sminax", "strike", "epi_q", "depth", "depth_unc",
    "depth_q", "mw", "mw_unc", "mw_q", "mw_src", "mo", "fac", "mo_auth",
    "mpp", "mpr", "mrr", "mrt", "mtp", "mtt",
    "str1", "dip1", "rake1", "str2", "dip2", "rake2", "mech_type", "eventid",
]
NUMERIC = ["lat", "lon", "smajax", "sminax", "strike", "depth", "depth_unc", "mw", "mw_unc",
           "mo", "fac", "mpp", "mpr", "mrr", "mrt", "mtp", "mtt",
           "str1", "dip1", "rake1", "str2", "dip2", "rake2"]


class ISCGEMError(ValueError):
    """The ISC-GEM file is missing or not in the expected format."""


def load_iscgem(path: str | Path, supplement: bool = False) -> pd.DataFrame:
    """One row per event; ``mw_src`` is "d" (from a moment) or "p" (proxy)."""
    path = Path(path)
    try:
        frame = pd.read_csv(path, comment="#", header=None, dtype=str,
                            skipinitialspace=True)
    except (OSError, ValueError) as exc:
        raise ISCGEMError(f"Katalog ISC-GEM tidak dapat dibaca: {path.name}") from exc
    if frame.shape[1] != len(COLUMNS):
        raise ISCGEMError(
            f"{path.name}: {frame.shape[1]} kolom, format ISC-GEM memiliki {len(COLUMNS)}.")
    frame.columns = COLUMNS
    frame = frame.apply(lambda column: column.str.strip())
    frame = frame.replace("", pd.NA)
    for column in NUMERIC:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["time"] = pd.to_datetime(frame["date"], utc=True, format="%Y-%m-%d %H:%M:%S.%f")
    frame["supplement"] = supplement
    return frame.drop(columns="date")


def in_region(frame: pd.DataFrame, region: dict[str, float]) -> pd.DataFrame:
    return frame.loc[frame["lat"].between(region["minlatitude"], region["maxlatitude"])
                     & frame["lon"].between(region["minlongitude"], region["maxlongitude"])]
