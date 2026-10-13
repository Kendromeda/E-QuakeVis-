from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import setup_psha_data as setup  # noqa: E402


def test_release_file_names_every_asset_the_parts_need():
    release = json.loads(setup.RELEASE_FILE.read_text(encoding="utf-8"))
    for keys in setup.PART_ASSETS.values():
        for key in keys:
            asset = release["assets"][key]
            assert len(asset["sha256"]) == 64 and asset["check"]
            assert ("extract_to" in asset) != ("save_as" in asset)


def test_safe_extract_writes_flat_archives_and_rejects_paths(tmp_path):
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as zf:
        zf.writestr("a.csv", "x\n1\n")
    assert setup.safe_extract(good, tmp_path / "out") == ["a.csv"]
    assert (tmp_path / "out" / "a.csv").read_text() == "x\n1\n"
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../evil.txt", "no")
    with pytest.raises(setup.SetupError):
        setup.safe_extract(bad, tmp_path / "out2")
    assert not (tmp_path / "evil.txt").exists()


def test_install_assets_checks_sha256(tmp_path, monkeypatch):
    payload = tmp_path / "src" / "v.npz"
    payload.parent.mkdir()
    payload.write_bytes(b"data")
    release = {"tag": "t", "repository": "o/r", "assets": {"vs30": {
        "name": "v.npz", "sha256": hashlib.sha256(b"data").hexdigest(),
        "save_as": "data/vs30/v.npz", "check": "data/vs30/v.npz"}}}

    def fake_fetch(release, name, folder):
        target = folder / name
        target.write_bytes(payload.read_bytes())
        return target

    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup, "fetch_asset", fake_fetch)
    setup.install_assets(release, ("vs30",))
    assert (tmp_path / "data/vs30/v.npz").read_bytes() == b"data"
    release["assets"]["vs30"]["sha256"] = "0" * 64
    with pytest.raises(setup.SetupError):
        setup.install_assets(release, ("vs30",))
