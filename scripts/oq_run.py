r"""Run an ``oq`` command with E-QuakeVis' own start-up memory threshold.

OpenQuake waits before starting a job while 80 % or more of RAM is in use and
logs "Memory occupation N%" every five seconds until memory is freed. That 80
is hardcoded in ``openquake.engine.engine.run_calc``, so this wrapper swaps the
module's ``psutil`` for a gate that applies
``utils.psha_jobs.OQ_MEMORY_LIMIT_PERCENT`` instead; OpenQuake itself is left
untouched and keeps its own ``hard_mem_limit`` protection during the run.

The arguments are those of ``python -m openquake.commands``::

    .venv-oq\Scripts\python.exe scripts\oq_run.py run job.ini -e csv
"""
from __future__ import annotations

from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.psha_jobs import OQ_MEMORY_LIMIT_PERCENT  # noqa: E402


class MemoryGate:
    """``psutil`` stand-in for the single memory check in ``run_calc``.

    Below ``limit`` it reports 0 % so OpenQuake's ``< 80`` test passes at once;
    at or above it the real reading is passed through, so OpenQuake still waits
    and still logs the true occupation. Every other attribute is the real
    ``psutil``, which the engine uses for its own limits during the run.
    """

    def __init__(self, psutil_module: object, limit: float) -> None:
        self._psutil = psutil_module
        self._limit = limit

    def virtual_memory(self):  # noqa: ANN201 - mirrors psutil's own signature
        memory = self._psutil.virtual_memory()
        if memory.percent < self._limit:
            return memory._replace(percent=0.0)
        return memory

    def __getattr__(self, name: str):  # noqa: ANN401 - plain delegation
        return getattr(self._psutil, name)


def main(argv: list[str]) -> int:
    from openquake.engine import engine

    engine.psutil = MemoryGate(engine.psutil, OQ_MEMORY_LIMIT_PERCENT)
    sys.argv = ["oq", *argv]
    runpy.run_module("openquake.commands", run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
