from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from utils.slab_grid import SlabGridError, load_slab_grid


def write_grid(path: Path, **changes) -> Path:
    fields = {
        "lon": np.array([100.0, 100.05, 100.1]),
        "lat": np.array([-5.0, -4.95]),
        "depth": np.array([[10.0, 20.0, np.nan], [30.0, 40.0, 50.0]]),
        "metadata": np.array(json.dumps({
            "id": "sum", "name": "Sumatra-Java (Sunda)",
            "source": "https://earthquake.usgs.gov/slab2/", "doi": "10.5066/F7PV6JNV",
        })),
    }
    np.savez_compressed(path, **{**fields, **changes})
    return path


def test_load_preserves_nodes_masks_and_positive_down_depth(tmp_path):
    result = load_slab_grid(write_grid(tmp_path / "sum.npz"))
    assert result["lon"] == [100.0, 100.05, 100.1]
    assert result["lat"] == [-5.0, -4.95]
    assert result["depth"] == [[10.0, 20.0, None], [30.0, 40.0, 50.0]]
    assert result["id"] == "sum"
    assert result["name"] == "Sumatra-Java (Sunda)"
    assert result["doi"] == "10.5066/F7PV6JNV"
    json.dumps(result, allow_nan=False)


def test_callers_cannot_mutate_cached_grid(tmp_path):
    path = write_grid(tmp_path / "sum.npz")
    result = load_slab_grid(path)
    result["depth"][0][0] = 900
    result["metadata"]["name"] = "changed"
    again = load_slab_grid(path)
    assert again["depth"][0][0] == 10
    assert again["metadata"]["name"] == "Sumatra-Java (Sunda)"


def test_missing_and_corrupt_grid_fail_without_fabricated_fallback(tmp_path):
    with pytest.raises(SlabGridError, match="tidak ditemukan"):
        load_slab_grid(tmp_path / "missing.npz")
    path = tmp_path / "bad.npz"
    path.write_text("corrupt")
    with pytest.raises(SlabGridError):
        load_slab_grid(path)


@pytest.mark.parametrize("changes", [
    {"lon": np.array([100.1, 100.05, 100.0])},
    {"lon": np.array([100.0, 100.05, 100.2])},
    {"lon": np.array([100.0, np.nan, 100.1])},
    {"lat": np.array([-91.0, -90.0])},
    {"depth": np.array([[10.0, 20.0]])},
    {"depth": np.array([[-1.0, 20.0, np.nan], [30.0, 40.0, 50.0]])},
    {"depth": np.full((2, 3), np.nan)},
    {"depth": np.array([[np.inf, 20.0, np.nan], [30.0, 40.0, 50.0]])},
    {"metadata": np.array("{}")},
])
def test_invalid_grid_is_rejected(tmp_path, changes):
    with pytest.raises(SlabGridError):
        load_slab_grid(write_grid(tmp_path / "bad.npz", **changes))


def test_bundled_sunda_grid_has_official_provenance_and_geological_extent():
    path = Path(__file__).resolve().parents[1] / "data" / "slab2" / "sum.npz"
    result = load_slab_grid(path)
    assert result["id"] == "sum"
    assert result["lon"][0] < 95 < 120 < result["lon"][-1]
    assert result["lat"][0] < -10 < 0 < result["lat"][-1]
    assert len(result["metadata"]["source_sha256"]) == 64
    depths = [z for row in result["depth"] for z in row if z is not None]
    assert min(depths) < 20 and max(depths) > 600


def test_converter_preserves_source_values_and_mask(tmp_path):
    h5py = pytest.importorskip("h5py", reason="conversion-only dependency")
    from scripts.build_slab2_grid import convert_grid

    source = tmp_path / "source.grd"
    original = np.array([
        [np.nan, np.nan, np.nan, np.nan],
        [np.nan, -20.5, -40.25, np.nan],
        [np.nan, np.nan, -60.0, np.nan],
        [np.nan, np.nan, np.nan, np.nan],
    ], dtype=np.float32)
    with h5py.File(source, "w") as handle:
        handle.create_dataset("x", data=[100, 100.05, 100.1, 100.15])
        handle.create_dataset("y", data=[-5, -4.95, -4.9, -4.85])
        handle.create_dataset("z", data=original)
    destination = tmp_path / "sum.npz"
    manifest = convert_grid(source, destination, "sum")
    result = load_slab_grid(destination)
    assert result["lon"] == [100.05, 100.1]
    assert result["lat"] == [-4.95, -4.9]
    assert result["depth"] == [[20.5, 40.25], [None, 60.0]]
    assert manifest["valid_nodes"] == 3
    assert manifest["source_shape"] == [4, 4]
    assert len(manifest["source_sha256"]) == 64


def test_converter_rejects_positive_elevation_in_depth_source(tmp_path):
    h5py = pytest.importorskip("h5py", reason="conversion-only dependency")
    from scripts.build_slab2_grid import convert_grid

    source = tmp_path / "source.grd"
    with h5py.File(source, "w") as handle:
        handle.create_dataset("x", data=[100, 100.05])
        handle.create_dataset("y", data=[-5, -4.95])
        handle.create_dataset("z", data=[[10.0, -20.0], [-30.0, -40.0]])
    with pytest.raises(ValueError, match="negative"):
        convert_grid(source, tmp_path / "sum.npz", "sum")


@pytest.mark.parametrize("model", ["sum", "sul", "hal", "png", "phi"])
def test_bundled_grids_exactly_match_published_nodes_and_masks(model):
    import hashlib
    h5py = pytest.importorskip("h5py", reason="source verification dependency")
    root = Path(__file__).resolve().parents[1] / "data" / "slab2"
    result = load_slab_grid(root / f"{model}.npz")
    meta = result["metadata"]
    source = root / meta["source_file"]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == meta["source_sha256"]
    r0, r1, c0, c1 = meta["source_crop_indices"]
    with h5py.File(source) as original:
        expected = -original["z"][r0:r1, c0:c1]
        np.testing.assert_array_equal(result["lon"], original["x"][c0:c1])
        np.testing.assert_array_equal(result["lat"], original["y"][r0:r1])
    actual = np.asarray(result["depth"], dtype=float)
    np.testing.assert_array_equal(np.isnan(actual), np.isnan(expected))
    np.testing.assert_array_equal(actual, expected)
    assert int(np.isfinite(actual).sum()) == meta["valid_nodes"]
