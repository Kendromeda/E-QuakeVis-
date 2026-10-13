"""OpenQuake parts of the Tahap B source model (needs the OpenQuake environment).

- Background seismicity: one ``multiPointSource`` per depth layer with the
  smoothed a-values from the catalogue. Rupture settings (nodal planes,
  magnitude scaling, aspect ratio, seismogenic depths, hypocentral depth)
  come from the matching gridded layer of GEM IDN v2024, so the model differs
  from GEM only in where and how often earthquakes happen.
- Faults and megathrust: the PuSGeN 2024 sources of GEM IDN v2024, kept when
  they lie within the maximum distance of the site box. Their geometry and
  slip-rate-based rates cannot be derived from a catalogue.
- A source logic tree with GEM's two megathrust segmentation branches.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from utils.magnitude_homogenization import haversine_km

GEM_DIR = Path(__file__).resolve().parents[1] / "data" / "psha" / "gem_idn_v2024.0.0" / "in"
GRIDDED = [GEM_DIR / "ssm" / "gridded" / f"layer{i}.xml" for i in range(1, 7)]
FAULT_FILES = {
    "crust": [GEM_DIR / "ssm" / "crust" / "Indo_Faults_GR.xml",
              GEM_DIR / "ssm" / "crust" / "Indo_Faults_CH.xml"],
    "interface_S1": [GEM_DIR / "ssm" / "interface" / "Sub_Indo_GR_S1.xml",
                     GEM_DIR / "ssm" / "interface" / "Sub_Indo_CH_S1.xml"],
    "interface_S2": [GEM_DIR / "ssm" / "interface" / "Sub_Indo_GR_S2.xml",
                     GEM_DIR / "ssm" / "interface" / "Sub_Indo_CH_S2.xml"],
}
# Weights of GEM's two megathrust segmentation models (ssmLT_clean.xml).
SEGMENTATION_WEIGHTS = {"interface_S1": 0.9, "interface_S2": 0.1}
MFD_BIN = 0.1


def _converter():
    from openquake.hazardlib.sourceconverter import SourceConverter

    return SourceConverter(investigation_time=1.0, rupture_mesh_spacing=5.0,
                           complex_fault_mesh_spacing=20.0, width_of_mfd_bin=MFD_BIN,
                           area_source_discretization=10.0)


def read_sources(path: Path) -> list:
    from openquake.hazardlib import nrml

    return [src for group in nrml.to_python(str(path), _converter()) for src in group]


def gem_layer_templates() -> list:
    """The six GEM gridded sources (rupture settings are reused, not their rates)."""
    return [read_sources(path)[0] for path in GRIDDED]


def hypo_depth(src) -> float:
    return float(src.hypocenter_distribution.data[0][1])


def template_for_layer(templates: list, top: float, bottom: float):
    """GEM layer whose hypocentral depth lies in [top, bottom), else the nearest."""
    inside = [t for t in templates if top <= hypo_depth(t) < bottom]
    if inside:
        return inside[0]
    middle = (top + bottom) / 2
    return min(templates, key=lambda t: abs(hypo_depth(t) - middle))


def background_source(template, source_id: str, name: str, lons, lats, a_values,
                      b_value: float, mmin: float, mmax: float):
    """A multiPointSource with truncated G-R MFDs at the given points."""
    from openquake.hazardlib.geo.mesh import Mesh
    from openquake.hazardlib.mfd.multi_mfd import MultiMFD
    from openquake.hazardlib.source.multi_point import MultiPointSource

    n = len(lons)
    mfd = MultiMFD("truncGutenbergRichterMFD", size=n, width_of_mfd_bin=MFD_BIN,
                   min_mag=[mmin], max_mag=[mmax], bin_width=[MFD_BIN],
                   a_val=np.asarray(a_values, dtype=float), b_val=[b_value])
    return MultiPointSource(
        source_id, name, template.tectonic_region_type, mfd,
        template.magnitude_scaling_relationship, template.rupture_aspect_ratio,
        template.upper_seismogenic_depth, template.lower_seismogenic_depth,
        template.nodal_plane_distribution, template.hypocenter_distribution,
        Mesh(np.asarray(lons, dtype=float), np.asarray(lats, dtype=float)))


def source_points(src) -> tuple[np.ndarray, np.ndarray]:
    """Longitudes and latitudes outlining a fault or megathrust source."""
    if hasattr(src, "fault_trace"):
        points = list(src.fault_trace)
    elif hasattr(src, "edges"):
        points = [p for edge in src.edges for p in edge]
    else:
        polygon = src.polygon
        return np.asarray(polygon.lons), np.asarray(polygon.lats)
    return (np.array([p.longitude for p in points]), np.array([p.latitude for p in points]))


def distance_to_box_km(lons: np.ndarray, lats: np.ndarray, box: tuple) -> float:
    """Smallest distance (km) from the points to the box (0 when inside)."""
    west, south, east, north = box
    near_lon = np.clip(lons, west, east)
    near_lat = np.clip(lats, south, north)
    return float(np.min(haversine_km(lats, lons, near_lat, near_lon)))


def mfd_summary(src) -> dict:
    """Magnitude range and annual rates of a source's MFD."""
    rates = src.mfd.get_annual_occurrence_rates()
    mags = [m for m, _ in rates]
    return {"mfd_type": type(src.mfd).__name__,
            "mag_min": round(float(min(mags)), 2) if mags else np.nan,
            "mag_max": round(float(max(mags)), 2) if mags else np.nan,
            "annual_rate_total": float(sum(r for _, r in rates)),
            "annual_rate_mw_ge_7": float(sum(r for m, r in rates if m >= 7.0 - 1e-9))}


def select_faults(box: tuple, crust_km: float, interface_km: float) -> dict:
    """{group: [(source, distance_km)]} of GEM faults/megathrust near the box."""
    selected = {}
    for group, paths in FAULT_FILES.items():
        limit = interface_km if group.startswith("interface") else crust_km
        kept = []
        for path in paths:
            for src in read_sources(path):
                dist = distance_to_box_km(*source_points(src), box)
                if dist <= limit:
                    kept.append((src, dist))
        selected[group] = kept
    return selected


def write_sources(path: Path, sources: list, name: str) -> None:
    from openquake.hazardlib.sourcewriter import write_source_model

    write_source_model(str(path), sources, name=name)


def write_logic_tree(path: Path, background: list[str], crust: list[str],
                     interface: dict[str, list[str]]) -> None:
    """Source logic tree: background + faults with each megathrust segmentation branch."""
    branches = []
    for i, (group, files) in enumerate(interface.items(), 1):
        models = "\n                ".join(background + files + crust)
        branches.append(f"""            <logicTreeBranch branchID="b{i}">
                <uncertaintyModel>
                {models}
                </uncertaintyModel>
                <uncertaintyWeight>{SEGMENTATION_WEIGHTS[group]}</uncertaintyWeight>
            </logicTreeBranch>""")
    path.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<nrml xmlns:gml="http://www.opengis.net/gml" xmlns="http://openquake.org/xmlns/nrml/0.4">
    <logicTree logicTreeID="lt1">
        <logicTreeBranchSet uncertaintyType="sourceModel" branchSetID="bs1">
{chr(10).join(branches)}
        </logicTreeBranchSet>
    </logicTree>
</nrml>
""", encoding="utf-8")
