"""State of a PSHA wizard project (choices and confirmations per step).

A project is one JSON file in ``data/psha/projects/``. Each wizard step must
be confirmed in order; changing an earlier step withdraws the confirmation of
every later step, so a run always reflects choices the user has reviewed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = ROOT / "data" / "psha" / "projects"
PSHA_DIR = ROOT / "data" / "psha"

STEPS = ("region", "source", "gmpe", "review", "result")
STEP_TITLES = {
    "region": "1. Wilayah dan kondisi situs",
    "source": "2. Model sumber gempa",
    "gmpe": "3. GMPE dan logic tree",
    "review": "4. Tinjau dan hitung",
    "result": "5. Hasil",
}


class ProjectError(ValueError):
    """A project file is missing or invalid."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "proyek"


def new_project(name: str, projects_dir: Path | None = None) -> dict[str, Any]:
    projects_dir = projects_dir or PROJECTS_DIR
    base = slugify(name)
    project_id, counter = base, 2
    while (projects_dir / f"{project_id}.json").exists():
        project_id, counter = f"{base}_{counter}", counter + 1
    project = {
        "id": project_id,
        "name": name.strip() or project_id,
        "created_utc": _now(),
        "updated_utc": _now(),
        "region": {"provinces": [], "spacing": 0.2, "vs30": 760.0},
        "source_model": {"kind": "gem", "version": "v2024.0.0"},
        "gmpe_logic_tree": {},
        "notes": {},
        "confirmed": {},
        "run_id": None,
    }
    save_project(project, projects_dir)
    return project


def save_project(project: dict[str, Any], projects_dir: Path | None = None) -> None:
    projects_dir = projects_dir or PROJECTS_DIR
    projects_dir.mkdir(parents=True, exist_ok=True)
    project["updated_utc"] = _now()
    path = projects_dir / f"{project['id']}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(project, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_project(project_id: str, projects_dir: Path | None = None) -> dict[str, Any]:
    projects_dir = projects_dir or PROJECTS_DIR
    path = projects_dir / f"{project_id}.json"
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProjectError(f"Proyek {project_id} tidak dapat dibaca.") from exc
    missing = [key for key in ("id", "region", "source_model", "gmpe_logic_tree", "confirmed")
               if key not in project]
    if missing:
        raise ProjectError(f"Proyek {project_id} tidak lengkap: {', '.join(missing)}.")
    return project


def list_projects(projects_dir: Path | None = None) -> list[dict[str, Any]]:
    projects_dir = projects_dir or PROJECTS_DIR
    if not projects_dir.is_dir():
        return []
    projects = []
    for path in projects_dir.glob("*.json"):
        try:
            projects.append(load_project(path.stem, projects_dir))
        except ProjectError:
            continue
    return sorted(projects, key=lambda p: p.get("updated_utc", ""), reverse=True)


def is_confirmed(project: dict[str, Any], step: str) -> bool:
    return step in project.get("confirmed", {})


def confirm_step(project: dict[str, Any], step: str) -> None:
    """Confirm ``step``; every earlier step must already be confirmed."""
    index = STEPS.index(step)
    for earlier in STEPS[:index]:
        if not is_confirmed(project, earlier):
            raise ProjectError(f"Langkah {STEP_TITLES[earlier]} belum dikonfirmasi.")
    project.setdefault("confirmed", {})[step] = _now()


def withdraw_from(project: dict[str, Any], step: str) -> list[str]:
    """Withdraw the confirmation of ``step`` and all later steps; return them."""
    index = STEPS.index(step)
    withdrawn = [s for s in STEPS[index:] if s in project.get("confirmed", {})]
    for s in withdrawn:
        del project["confirmed"][s]
    if index <= STEPS.index("review"):
        project["run_id"] = None
    return withdrawn


def current_step(project: dict[str, Any]) -> str:
    """The first step that is not confirmed yet (``result`` when all are)."""
    return next((s for s in STEPS if not is_confirmed(project, s)), STEPS[-1])


def unlocked(project: dict[str, Any], step: str) -> bool:
    index = STEPS.index(step)
    return all(is_confirmed(project, s) for s in STEPS[:index])


# --- Source model inspection -------------------------------------------------

_TRT = re.compile(rb'tectonicRegion="([^"]+)"')
_SOURCE = re.compile(rb"<(\w+Source)\b")
_LT_FILES = re.compile(r"<uncertaintyModel>(.*?)</uncertaintyModel>", re.S)


def model_input_dir(version: str) -> Path:
    return PSHA_DIR / f"gem_idn_{version}" / "in"


def _source_logic_tree(version: str) -> Path:
    directory = model_input_dir(version)
    for name in ("ssmLT_clean.xml", "ssmLT.xml"):
        if (directory / name).is_file():
            return directory / name
    raise ProjectError(f"Logic tree sumber model GEM IDN {version} tidak ditemukan.")


@lru_cache(maxsize=4)
def _summary_cached(version: str, mtime_ns: int) -> tuple[tuple[str, str, int, int], ...]:
    del mtime_ns  # cache key only
    lt_path = _source_logic_tree(version)
    files: list[str] = []
    for block in _LT_FILES.findall(lt_path.read_text(encoding="utf-8")):
        files.extend(block.split())
    rows = []
    for name in dict.fromkeys(files):
        payload = (lt_path.parent / name).read_bytes()
        trts = {m.decode() for m in _TRT.findall(payload)}
        rows.append((name, ", ".join(sorted(trts)), len(_SOURCE.findall(payload)),
                     len(payload)))
    return tuple(rows)


def source_model_summary(version: str) -> list[dict[str, Any]]:
    """Files of the source model with their TRT and number of source elements."""
    lt_path = _source_logic_tree(version)
    rows = _summary_cached(version, lt_path.stat().st_mtime_ns)
    return [{"file": f, "trt": t, "sources": n, "bytes": b} for f, t, n, b in rows]


def source_model_trts(version: str) -> list[str]:
    """Tectonic region types present in the source model, in a fixed order."""
    found = {trt.strip() for row in source_model_summary(version)
             for trt in row["trt"].split(",") if trt.strip()}
    order = ["Active Shallow Crust", "Subduction Interface", "Subduction Intraslab",
             "Subduction Inslab"]
    return [t for t in order if t in found] + sorted(found - set(order))
