"""Download a GEM OpenQuake input model for Indonesia (IDN).

Two versions are supported (``--version``):

- ``v2024.0.0`` (default): the PuSGeN 2024 model as implemented by GEM for the
  2026 mosaic, CC BY-NC-SA 4.0 (non-commercial use only). It is published as
  ``job.zip``, which is extracted to ``in/``.
- ``v2017.1.0``: Irsyam et al. (2017) as implemented by GEM, CC BY-SA 4.0.

Files are fetched through the public Nextcloud share's WebDAV endpoint and
recorded with SHA-256 in ``manifest.json``. Run
``python scripts/download_gem_idn_model.py [--version v2017.1.0]``; pass
``--verify`` to only check an existing download against the manifest.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PSHA_DIR = ROOT / "data" / "psha"
WEBDAV_URL = "https://cloud.openquake.org/public.php/webdav/"
PRODUCT_URL = "https://www.globalquakemodel.org/product/indonesia-hazard"
DOCUMENTATION_URL = "https://hazard.openquake.org/gem/pdf/idn-report.pdf"
MODELS = {
    "v2024.0.0": {
        "share_token": "wCBESckQYgWgLqx",
        "authors": "Team for Updating Seismic Hazard Maps of Indonesia (PuSGeN, "
                   "Kementerian Pekerjaan Umum) and GEM Foundation",
        "license": "CC BY-NC-SA 4.0",
    },
    "v2017.1.0": {
        "share_token": "zsoZY55oe2gSwFZ",
        "authors": "Irsyam, M., Faizal, L., Natawidjaja, D., Meilano, I., Widiyantoro, S., "
                   "Triyoso, W., Rudiyanto, A., Hidayati, S., Asrurifak, M., Ridwan, M., "
                   "Cummins, P.",
        "license": "CC BY-SA 4.0",
    },
}
DEFAULT_VERSION = "v2024.0.0"


def model_dir(version: str) -> Path:
    return PSHA_DIR / f"gem_idn_{version}"


def _request(token: str, path: str, method: str = "GET", depth: str | None = None) -> Request:
    auth = base64.b64encode(f"{token}:".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    if depth is not None:
        headers["Depth"] = depth
    return Request(WEBDAV_URL + path, method=method, headers=headers)


def list_files(token: str) -> list[tuple[str, int]]:
    """Return (relative path, size) for every file in the public share."""
    with urlopen(_request(token, "", "PROPFIND", "infinity"), timeout=120) as response:
        body = response.read().decode("utf-8")
    files = []
    for entry in re.findall(r"<d:response>.*?</d:response>", body, re.S):
        href = re.search(r"<d:href>(.*?)</d:href>", entry).group(1)
        size = re.search(r"<d:getcontentlength>(\d+)</d:getcontentlength>", entry)
        if size:
            files.append((href.split("/public.php/webdav/", 1)[1], int(size.group(1))))
    if not files:
        raise RuntimeError("The GEM share returned no files.")
    return sorted(files)


def _record(relative: str, data: bytes) -> dict:
    return {"path": relative, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _write(destination: Path, relative: str, data: bytes) -> dict:
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return _record(relative, data)


def download(version: str = DEFAULT_VERSION) -> dict:
    info = MODELS[version]
    token = info["share_token"]
    destination = model_dir(version)
    records = []
    for relative, size in list_files(token):
        with urlopen(_request(token, relative), timeout=600) as response:
            data = response.read()
        if len(data) != size:
            raise RuntimeError(f"{relative}: expected {size} bytes, received {len(data)}")
        print(f"{relative} ({size:,} B)")
        if relative != "job.zip":
            records.append(_write(destination, relative, data))
            continue
        # v2024 ships the OpenQuake job as one archive; its paths are relative
        # to job.ini, so they are kept as they are under in/.
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                records.append(_write(destination, f"in/{member.filename}",
                                      archive.read(member)))
    manifest = {
        "name": "GEM OpenQuake input model for Indonesia (IDN)",
        "model_version": version,
        "authors": info["authors"],
        "implementation": "GEM Foundation Hazard Team",
        "license": info["license"],
        "source": f"https://cloud.openquake.org/s/{token}",
        "product_page": PRODUCT_URL,
        "documentation": DOCUMENTATION_URL,
        "snapshot_date": datetime.now(timezone.utc).date().isoformat(),
        "files": sorted(records, key=lambda record: record["path"]),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def verify(version: str = DEFAULT_VERSION) -> list[str]:
    """Return the files whose content no longer matches the manifest."""
    destination = model_dir(version)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    problems = []
    for record in manifest["files"]:
        path = destination / record["path"]
        if not path.exists():
            problems.append(f"missing: {record['path']}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            problems.append(f"changed: {record['path']}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", choices=list(MODELS), default=DEFAULT_VERSION)
    parser.add_argument("--verify", action="store_true", help="only check the existing download")
    args = parser.parse_args()
    if args.verify:
        problems = verify(args.version)
        print("\n".join(problems) or "All files match the manifest.")
        return 1 if problems else 0
    manifest = download(args.version)
    print(f"Downloaded {len(manifest['files'])} files to "
          f"{model_dir(args.version).relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
