from __future__ import annotations

from io import BytesIO

import numpy as np
import pandas as pd
from PIL import Image

from utils.publication_map import (
    PublicationHazardLayer,
    _publication_source_lines,
    _render_hazard_overlay,
    _sidebar_legend_block_height,
    capture_filtered_map,
    render_publication_map,
)

EMPTY = {"type": "FeatureCollection", "features": []}
RED, BLUE = "#ff0000", "#0000ff"


def hazard_layer(classes=((0, 1), (1, -1))) -> PublicationHazardLayer:
    return PublicationHazardLayer(
        classes=np.array(classes),
        bounds=(106.0, -7.0, 108.0, -5.0),
        colors=(RED, BLUE),
        labels=("0 – 0,5", "≥ 0,5"),
        heading="PGA 475 th (g)",
        source="Bahaya gempa: PSHA uji",
    )


def test_overlay_places_cells_at_their_coordinates():
    overlay = _render_hazard_overlay(hazard_layer(), (106.0, -7.0, 108.0, -5.0), 8, (200, 200))
    rgba = np.asarray(overlay)
    assert tuple(rgba[20, 20, :3]) == (255, 0, 0)       # north-west cell: class 0
    assert tuple(rgba[20, 180, :3]) == (0, 0, 255)      # north-east cell: class 1
    assert tuple(rgba[180, 20, :3]) == (0, 0, 255)      # south-west cell: class 1
    assert rgba[180, 180, 3] == 0                       # south-east: no site


def test_overlay_is_transparent_outside_the_grid():
    overlay = _render_hazard_overlay(hazard_layer(), (104.0, -9.0, 110.0, -3.0), 7, (300, 300))
    rgba = np.asarray(overlay)
    assert rgba[5, 5, 3] == 0 and rgba[295, 295, 3] == 0
    assert rgba[110, 110, 3] > 0                        # inside the north-west cell


def test_snapshot_without_earthquakes_draws_hazard_layer():
    snapshot = capture_filtered_map(
        pd.DataFrame(),
        EMPTY,
        EMPTY,
        hazard_layer=hazard_layer(((0, 0), (0, 0))),
        output_size=(320, 240),
        padding_percent=0,
        marker_scale_percent=100,
        show_basemap=False,
        show_earthquakes=False,
        show_faults=False,
        show_trenches=False,
        show_provinces=False,
        show_region_labels=False,
        selection_bounds=(106.0, -7.0, 108.0, -5.0),
    )
    with Image.open(BytesIO(snapshot)) as image:
        centre = image.convert("RGB").getpixel((160, 120))
    assert centre[0] > 200 and centre[1] < 120 and centre[2] < 120


def test_full_sheet_renders_with_hazard_legend():
    png_bytes, pdf_bytes = render_publication_map(
        pd.DataFrame(),
        EMPTY,
        EMPTY,
        hazard_layer=hazard_layer(),
        title="Peta Bahaya Gempa Uji",
        institution_name="Instansi Uji",
        show_basemap=False,
        show_earthquakes=False,
        show_faults=False,
        show_trenches=False,
        show_provinces=False,
        show_inset=False,
        selection_bounds=(106.0, -7.0, 108.0, -5.0),
    )
    assert png_bytes.startswith(b"\x89PNG") and pdf_bytes.startswith(b"%PDF")


def test_hazard_source_and_legend_space_are_included():
    lines = _publication_source_lines(
        "", "", show_basemap=False, show_earthquakes=False, show_faults=False,
        show_trenches=False, show_slabs=False, show_population_density=False,
        show_provinces=False, show_inset=False, fault_geojson=EMPTY,
        trench_geojson=None, slab_geojson=None, hazard_source="Bahaya gempa: PSHA uji",
    )
    assert lines == ["Bahaya gempa: PSHA uji"]

    common = dict(
        show_basemap=False, basemap_name="", show_basemap_legend=False,
        show_shortbread_roads=False, show_shortbread_rivers=False,
        show_shortbread_topography=False, show_trenches=False, trench_present=False,
        show_faults=False, show_slabs=False, slab_present=False, show_provinces=False,
        show_population_density=False, show_population_density_legend=False,
        has_earthquakes=False,
    )
    without = _sidebar_legend_block_height(1.0, 1.0, 100, **common)
    with_hazard = _sidebar_legend_block_height(1.0, 1.0, 100, hazard_class_count=12, **common)
    assert with_hazard - without == 8 + 27 + 6 * 24 + 6
