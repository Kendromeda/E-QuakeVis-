"""Set up the data PSHA needs after cloning, in one command.

Run in the OpenQuake environment::

    .venv-oq\\Scripts\\python.exe scripts\\setup_psha_data.py
    .venv-oq\\Scripts\\python.exe scripts\\setup_psha_data.py --only vs30 catalog
    .venv-oq\\Scripts\\python.exe scripts\\setup_psha_data.py --from-source

Parts (``--only`` picks some; what already exists is skipped unless ``--force``):

- ``gem``: GEM IDN v2024 source model (``scripts/download_gem_idn_model.py``),
  needed for every PSHA run.
- ``vs30``: USGS Vs30 cropped to Indonesia, for "Vs30 USGS per situs".
- ``catalog``: merged Mw catalogue, for Tahap B source models.

``vs30`` and ``catalog`` come ready-made from the GitHub Release named in
``data/psha/data_release.json`` (checked with SHA-256). The repository is
private, so the release is fetched with the GitHub CLI (``gh auth login``
once). With ``--from-source`` they are rebuilt from the original sources
instead: the USGS grid (610 MB) for ``vs30``; the ISC Bulletin download (many
hours) plus ``build_mw_catalog.py`` for ``catalog``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE_FILE = ROOT / "data" / "psha" / "data_release.json"
GEM_CHECK = ROOT / "data" / "psha" / "gem_idn_v2024.0.0" / "in" / "ssmLT_clean.xml"
USGS_VS30_URL = "https://apps.usgs.gov/shakemap_geodata/vs30/global_vs30.grd"
PARTS = ("gem", "vs30", "catalog")
# Release assets each part needs.
PART_ASSETS = {"vs30": ("vs30", "vs30_manifest"), "catalog": ("catalog",)}


class SetupError(RuntimeError):
    pass


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_extract(archive: Path, target: Path) -> list[str]:
    """Extract a flat zip (plain file names only) into ``target``."""
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        bad = [n for n in names if "/" in n or "\\" in n or n.startswith(".") or ":" in n]
        if bad:
            raise SetupError(f"Unexpected paths in {archive.name}: {bad[:3]}")
        target.mkdir(parents=True, exist_ok=True)
        for name in names:
            with zf.open(name) as src, (target / name).open("wb") as dst:
                shutil.copyfileobj(src, dst)
    return names


def run(command: list[str]) -> None:
    print("$", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


def fetch_asset(release: dict, name: str, folder: Path) -> Path:
    """Download one release asset with gh (private repo), else the public URL."""
    if shutil.which("gh"):
        result = subprocess.run(
            ["gh", "release", "download", release["tag"], "-R", release["repository"],
             "-p", name, "-D", str(folder), "--clobber"],
            capture_output=True, text=True)
        if result.returncode == 0 and (folder / name).is_file():
            return folder / name
        print(f"gh could not download {name}: {result.stderr.strip()[:200]}")
    url = (f"https://github.com/{release['repository']}/releases/download/"
           f"{release['tag']}/{name}")
    try:
        with urlopen(Request(url, headers={"User-Agent": "E-QuakeVis"}), timeout=120) as resp:
            path = folder / name
            with path.open("wb") as handle:
                shutil.copyfileobj(resp, handle)
            return path
    except OSError as exc:
        raise SetupError(
            f"Could not download {name} from the GitHub Release ({exc}). The repository is "
            "private: install the GitHub CLI and run 'gh auth login', or use --from-source."
        ) from exc


def install_assets(release: dict, keys: tuple[str, ...]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        for key in keys:
            asset = release["assets"][key]
            path = fetch_asset(release, asset["name"], Path(tmp))
            if sha256(path) != asset["sha256"]:
                raise SetupError(f"SHA-256 of {asset['name']} does not match "
                                 f"{RELEASE_FILE.name}; not installed.")
            if "extract_to" in asset:
                names = safe_extract(path, ROOT / asset["extract_to"])
                print(f"  {asset['name']}: {len(names)} files -> {asset['extract_to']}")
            else:
                dest = ROOT / asset["save_as"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, dest)
                print(f"  {asset['name']} -> {asset['save_as']}")


def present(part: str, release: dict) -> bool:
    if part == "gem":
        return GEM_CHECK.is_file()
    return all((ROOT / release["assets"][key]["check"]).is_file() for key in PART_ASSETS[part])


def from_source(part: str) -> None:
    python = sys.executable
    if part == "vs30":
        grd = ROOT / "data" / "vs30" / "download" / "global_vs30.grd"
        if not grd.is_file():
            grd.parent.mkdir(parents=True, exist_ok=True)
            print(f"Downloading {USGS_VS30_URL} (about 610 MB)...", flush=True)
            with urlopen(Request(USGS_VS30_URL, headers={"User-Agent": "E-QuakeVis"}),
                         timeout=600) as resp, grd.open("wb") as handle:
                shutil.copyfileobj(resp, handle)
        run([python, "scripts/prepare_usgs_vs30.py"])
    elif part == "catalog":
        print("Rebuilding the catalogue from the ISC Bulletin takes many hours; the download "
              "resumes if interrupted. ISC-GEM (data/psha/iscgem/) is used when present.")
        run([python, "scripts/download_isc_bulletin.py"])
        run([python, "scripts/build_mw_catalog.py"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", choices=PARTS, default=list(PARTS))
    parser.add_argument("--from-source", action="store_true",
                        help="rebuild vs30/catalog from the original sources")
    parser.add_argument("--force", action="store_true", help="redo parts that already exist")
    args = parser.parse_args()
    release = json.loads(RELEASE_FILE.read_text(encoding="utf-8"))

    for part in args.only:
        if present(part, release) and not args.force:
            print(f"[{part}] already present, skipped")
            continue
        print(f"[{part}]", flush=True)
        try:
            if part == "gem":
                run([sys.executable, "scripts/download_gem_idn_model.py"])
            elif args.from_source:
                from_source(part)
            else:
                install_assets(release, PART_ASSETS[part])
        except (SetupError, subprocess.CalledProcessError, OSError) as exc:
            print(f"[{part}] failed: {exc}", file=sys.stderr)
            return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
