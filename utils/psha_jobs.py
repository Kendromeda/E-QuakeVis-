"""Launch and monitor ``scripts/run_psha.py`` runs from the app.

OpenQuake lives in its own environment (``.venv-oq``), so the app starts the
script there as a detached background process and tracks it through files in
``data/psha/results/<run_id>/``:

- ``app_job.json``: what the app launched (pid, area, spacing, estimate)
- ``run.log``: combined stdout/stderr of the run
- ``manifest.json``: written by the script only when the run finished
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
RUN_SCRIPT = ROOT / "scripts" / "run_psha.py"
OQ_PYTHON = ROOT / ".venv-oq" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
DEFAULT_OQ_DATADIR = Path("D:/oqdata")
JOB_FILE = "app_job.json"
LOG_FILE = "run.log"

# Measured: West Java, 382 sites at 0.1 deg in 20.6 min on 12 cores / 16 GB, of
# which about 4 min went to reading and preprocessing the source model.
FIXED_OVERHEAD_SECONDS = 240.0
SECONDS_PER_SITE = (20.6 * 60 - FIXED_OVERHEAD_SECONDS) / 382
# The West Java run used GEM v2017's 13 GMPEs; the per-site cost scales with
# the number of GMPEs in the logic tree.
BASELINE_GMPES = 13
GMM_LT_FILE = "gmm_logic_tree.json"
# OpenQuake workers. Each worker holds part of the source model in RAM; with all
# 12 cores the NTT v2024 run hit OpenQuake's 99% memory limit in preclassical
# on this 16 GB laptop, so runs use fewer workers (slower, far less RAM).
DEFAULT_OQ_CORES = 4
# Antivirus software (Avast) sets SSLKEYLOGFILE to a device path; OpenSSL then
# tries to open it and the OpenQuake process dies at start-up with
# "OPENSSL_Uplink ... no OPENSSL_Applink". OpenQuake never needs it.
UNSAFE_ENV = ("SSLKEYLOGFILE",)
SPACING_OPTIONS = (0.01, 0.05, 0.1, 0.2, 0.25, 0.5)
# Above this many sites a run takes most of a day on the reference laptop
# (about 2.6 s per site with 13 GMPEs); the wizard asks for a smaller area.
MAX_SITES = 10_000
# GEM IDN models known to scripts/run_psha.py, newest (default) first.
MODEL_VERSIONS = ("v2024.0.0", "v2017.1.0")
# RAM use (%) at or above which OpenQuake waits instead of starting a job.
# OpenQuake hardcodes 80 in ``openquake.engine.engine.run_calc``; on this 16 GB
# laptop the app, a browser and a chat client sit above that while a run of a
# few hundred sites still finishes, so scripts/oq_run.py raises it to this value.
OQ_MEMORY_LIMIT_PERCENT = 95

_PROGRESS = re.compile(r"\] (\w+)\s+(\d{1,3})% \[")
# While it waits, OpenQuake logs this line every few seconds until memory frees up.
_MEMORY_WAIT = re.compile(r"Memory occupation (\d+)%")


class PSHAJobError(RuntimeError):
    """A run cannot be started or managed."""


@dataclass(frozen=True)
class PSHAJob:
    run_id: str
    path: Path
    info: dict[str, Any]
    state: str  # "running", "finished" or "failed"

    @property
    def log_path(self) -> Path:
        return self.path / LOG_FILE

    @property
    def started(self) -> datetime:
        return datetime.fromisoformat(self.info["started_utc"])

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return max(0.0, (now - self.started).total_seconds())


def oq_available(python: Path = OQ_PYTHON) -> bool:
    return python.is_file()


def estimate_runtime_seconds(num_sites: int, n_gmpes: int = BASELINE_GMPES) -> float:
    return FIXED_OVERHEAD_SECONDS + SECONDS_PER_SITE * num_sites * n_gmpes / BASELINE_GMPES


def format_duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if not hours:
        return f"{minutes} menit"
    return f"{hours} jam {minutes} menit" if minutes else f"{hours} jam"


def memory_percent() -> float | None:
    """System RAM in use (%), the figure OpenQuake checks against the limit."""
    if os.name != "nt":
        try:
            import psutil
        except ImportError:
            return None
        return float(psutil.virtual_memory().percent)
    import ctypes

    class MemoryStatus(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatus()
    status.dwLength = ctypes.sizeof(MemoryStatus)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return 100.0 * (1 - status.ullAvailPhys / status.ullTotalPhys)


def _gmpe_count(logic_tree: dict | None) -> int:
    if not logic_tree:
        return BASELINE_GMPES
    return max(1, sum(len(branches) for branches in logic_tree.values()))


def spacing_slug(spacing: float) -> str:
    return f"{spacing:g}".replace(".", "p")


def make_run_id(
    provinces: Sequence[str],
    spacing: float,
    all_provinces: Sequence[str],
    results_dir: Path,
    model_version: str = MODEL_VERSIONS[0],
) -> str:
    """Readable, unused folder name such as ``jawa_tengah-banten_0p2_v2024``."""
    if not provinces or set(provinces) >= set(all_provinces):
        area = "indonesia"
    elif len(provinces) <= 3:
        area = "-".join(re.sub(r"[^a-z0-9]+", "_", p.lower()).strip("_") for p in provinces)
    else:
        area = f"{len(provinces)}_provinsi"
    base = f"{area}_{spacing_slug(spacing)}_{model_version.split('.')[0]}"
    run_id, counter = base, 2
    while (results_dir / run_id).exists():
        run_id, counter = f"{base}_{counter}", counter + 1
    return run_id


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def list_jobs(results_dir: Path) -> list[PSHAJob]:
    """Runs launched from the app, newest first."""
    if not results_dir.is_dir():
        return []
    jobs = []
    for path in results_dir.iterdir():
        job_file = path / JOB_FILE
        if not job_file.is_file():
            continue
        try:
            info = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if (path / "manifest.json").is_file():
            state = "finished"
        elif pid_alive(int(info.get("pid", 0))):
            state = "running"
        else:
            state = "failed"
        jobs.append(PSHAJob(path.name, path, info, state))
    return sorted(jobs, key=lambda job: job.info.get("started_utc", ""), reverse=True)


def active_job(results_dir: Path) -> PSHAJob | None:
    return next((job for job in list_jobs(results_dir) if job.state == "running"), None)


def start_job(
    run_id: str,
    provinces: Sequence[str],
    spacing: float,
    num_sites: int,
    results_dir: Path,
    python: Path = OQ_PYTHON,
    oq_datadir: Path | None = DEFAULT_OQ_DATADIR,
    model_version: str = MODEL_VERSIONS[0],
    gmm_logic_tree: dict | None = None,
    project_id: str | None = None,
    kabupaten: Sequence[str] = (),
    bounds: Sequence[float] | None = None,
    source_model: Path | None = None,
    vs30_source: str = "reference",
) -> PSHAJob:
    """Start ``run_psha.py`` in the background; one run at a time.

    ``gmm_logic_tree`` ({trt: [{gmpe, weight}]}) replaces the model's own GMPE
    logic tree; ``project_id`` links the run to a wizard project. ``bounds``
    (west, south, east, north) limits the sites to a box, and ``source_model``
    (a Tahap B folder from ``scripts/build_source_model.py``) replaces GEM's
    sources.
    """
    if source_model is not None and not (Path(source_model) / "ssmLT.xml").is_file():
        raise PSHAJobError(f"Model sumber {source_model} belum lengkap (tidak ada ssmLT.xml).")
    if not oq_available(python):
        raise PSHAJobError(f"Lingkungan OpenQuake tidak ditemukan: {python}")
    if active_job(results_dir) is not None:
        raise PSHAJobError("Masih ada perhitungan PSHA yang berjalan. Tunggu sampai selesai.")
    if not re.fullmatch(r"[A-Za-z0-9_\-]+", run_id):
        raise PSHAJobError("Nama run hanya boleh berisi huruf, angka, _ dan -.")
    if model_version not in MODEL_VERSIONS:
        raise PSHAJobError(f"Model tidak dikenal: {model_version}")
    if not (ROOT / "data" / "psha" / f"gem_idn_{model_version}" / "manifest.json").is_file():
        raise PSHAJobError(
            f"Model GEM IDN {model_version} belum diunduh. Jalankan "
            f"scripts/download_gem_idn_model.py --version {model_version}.")
    run_dir = results_dir / run_id
    if run_dir.exists():
        raise PSHAJobError(f"Folder hasil {run_id} sudah ada.")
    run_dir.mkdir(parents=True)

    command = [str(python), str(RUN_SCRIPT), "--run-id", run_id, "--model", model_version,
               "--spacing", f"{spacing:g}"]
    if kabupaten:
        command += ["--kabupaten", *kabupaten]
    elif provinces:
        command += ["--provinces", *provinces]
    if bounds is not None:
        command += ["--bounds", *(f"{v:g}" for v in bounds)]
    if source_model is not None:
        command += ["--source-model", str(source_model)]
    if vs30_source != "reference":
        command += ["--vs30-source", vs30_source]
    if gmm_logic_tree is not None:
        lt_path = run_dir / GMM_LT_FILE
        lt_path.write_text(json.dumps(gmm_logic_tree, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
        command += ["--gmm-lt-json", str(lt_path)]
    env = {key: value for key, value in os.environ.items()
           if key not in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV") + UNSAFE_ENV}
    env["PYTHONUNBUFFERED"] = "1"
    env.setdefault("OQ_NUM_CORES", str(DEFAULT_OQ_CORES))
    if "OQ_DATADIR" not in env and oq_datadir is not None and oq_datadir.parent.exists():
        env["OQ_DATADIR"] = str(oq_datadir)

    pid = _spawn(command, env, run_dir / LOG_FILE)
    info = {
        "run_id": run_id,
        "pid": pid,
        "started_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "provinces": list(provinces),
        "kabupaten": list(kabupaten),
        "bounds": list(bounds) if bounds is not None else None,
        "source_model": str(source_model) if source_model is not None else None,
        "vs30_source": vs30_source,
        "spacing_degrees": spacing,
        "model_version": model_version,
        "project_id": project_id,
        "num_sites": num_sites,
        "estimated_seconds": round(estimate_runtime_seconds(
            num_sites, _gmpe_count(gmm_logic_tree))),
        "command": command,
    }
    (run_dir / JOB_FILE).write_text(
        json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return PSHAJob(run_id, run_dir, info, "running")


# Starts the run and prints its pid. Used on Windows so that the run is not a
# child of the app: stopping the app (which may kill its whole process tree)
# then leaves the run alone.
_LAUNCHER = """import subprocess, sys
log = open(sys.argv[1], "ab")
process = subprocess.Popen(sys.argv[3:], stdin=subprocess.DEVNULL, stdout=log,
                           stderr=subprocess.STDOUT, creationflags=int(sys.argv[2]))
print(process.pid)
"""


def _spawn(command: list[str], env: dict[str, str], log_path: Path) -> int:
    """Start ``command`` detached from the app, logging to ``log_path``; return its pid."""
    if os.name != "nt":
        with log_path.open("ab") as log:
            return subprocess.Popen(
                command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True).pid
    flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    try:
        result = subprocess.run(
            [command[0], "-c", _LAUNCHER, str(log_path), str(flags), *command],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return int(result.stdout.strip().splitlines()[-1])
    except (subprocess.SubprocessError, ValueError, IndexError) as exc:
        raise PSHAJobError(f"Proses OpenQuake gagal dijalankan: {exc}") from exc


def cancel_job(job: PSHAJob) -> None:
    """Stop a running job and its OpenQuake worker processes."""
    pid = int(job.info["pid"])
    if not pid_alive(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, check=False,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        import signal

        os.killpg(pid, signal.SIGTERM)


def delete_job(job: PSHAJob, results_dir: Path) -> None:
    """Remove the folder of a failed app-launched run."""
    if job.state != "failed":
        raise PSHAJobError("Hanya run yang gagal yang dapat dihapus dari aplikasi.")
    if job.path.resolve().parent != results_dir.resolve():
        raise PSHAJobError("Folder run berada di luar folder hasil PSHA.")
    shutil.rmtree(job.path)


def log_tail(job: PSHAJob, lines: int = 12) -> str:
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def latest_progress(job: PSHAJob) -> tuple[str, int] | None:
    """Last OpenQuake ``<stage> NN%`` progress line in the log, if any."""
    try:
        text = job.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    matches = _PROGRESS.findall(text)
    if not matches:
        return None
    stage, percent = matches[-1]
    return stage, min(int(percent), 100)



def waiting_for_memory(job: PSHAJob) -> int | None:
    """RAM use (%) when OpenQuake is waiting for memory to be freed, else None."""
    lines = log_tail(job, lines=1).strip()
    match = _MEMORY_WAIT.search(lines)
    return int(match.group(1)) if match else None
