from __future__ import annotations

import os
from pathlib import Path
import socket
import sys
import threading
import time
import traceback
import webbrowser

from streamlit.web import cli as streamlit_cli


APP_NAME = "E-QuakeVis"
BOOTSTRAP_SOURCE = "from desktop_entry import main\nmain()\n"
_SERVER_LOG_STREAM = None


def get_runtime_dir() -> Path:
    """Return a stable directory for Streamlit's small source entrypoint."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base_dir = Path(local_app_data) if local_app_data else Path.home()
    return base_dir / APP_NAME / "runtime"


def ensure_streamlit_entrypoint() -> Path:
    """Create the source file Streamlit needs to manage script sessions."""
    runtime_dir = get_runtime_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    entrypoint = runtime_dir / "streamlit_entry.py"
    if not entrypoint.exists() or entrypoint.read_text(encoding="utf-8") != BOOTSTRAP_SOURCE:
        temporary = entrypoint.with_suffix(".tmp")
        temporary.write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
        os.replace(temporary, entrypoint)
    return entrypoint


def find_available_port() -> int:
    """Ask Windows for an unused loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def open_browser_when_ready(port: int, timeout_seconds: float = 45.0) -> None:
    """Open the browser only after the local Streamlit server is reachable."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                webbrowser.open(f"http://127.0.0.1:{port}", new=1)
                return
        except OSError:
            time.sleep(0.15)


def configure_compiled_output() -> None:
    """Give Click/Streamlit valid output handles in a windowed Nuitka build."""
    global _SERVER_LOG_STREAM
    if "__compiled__" not in globals():
        return

    log_path = get_runtime_dir() / "server.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _SERVER_LOG_STREAM = log_path.open("a", encoding="utf-8", buffering=1)
    sys.stdout = _SERVER_LOG_STREAM
    sys.stderr = _SERVER_LOG_STREAM


def main() -> int:
    configure_compiled_output()
    entrypoint = ensure_streamlit_entrypoint()
    configured_port = os.environ.get("E_QUAKEVIS_PORT") or os.environ.get(
        "DATABASE_INTERAKTIF_PORT"
    )
    port = int(configured_port) if configured_port else find_available_port()

    no_browser = os.environ.get("E_QUAKEVIS_NO_BROWSER") or os.environ.get(
        "DATABASE_INTERAKTIF_NO_BROWSER"
    )
    if no_browser != "1":
        browser_thread = threading.Thread(
            target=open_browser_when_ready,
            args=(port,),
            daemon=True,
            name="open-streamlit-browser",
        )
        browser_thread.start()

    sys.argv = [
        "streamlit",
        "run",
        str(entrypoint),
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--server.headless=true",
        "--server.fileWatcherType=none",
        "--server.runOnSave=false",
        "--runner.magicEnabled=false",
        "--global.developmentMode=false",
        "--browser.gatherUsageStats=false",
        # Brand accent for every widget (sliders, toggles, checkboxes,
        # multiselect, focus). The compiled EXE runs from an unpredictable
        # working directory, so the project .streamlit/config.toml is not
        # picked up here; set the theme colour explicitly instead.
        "--theme.primaryColor=#0b5ea8",
    ]
    return int(streamlit_cli.main())


def write_startup_log(message: str) -> None:
    """Keep startup diagnostics available when Windows has no visible console."""
    try:
        log_path = get_runtime_dir() / "startup.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


if __name__ == "__main__":
    write_startup_log(f"launcher started; executable={sys.executable}")
    try:
        exit_code = main()
    except BaseException:
        write_startup_log(traceback.format_exc())
        raise
    write_startup_log(f"launcher stopped normally; exit_code={exit_code}")
    raise SystemExit(exit_code)
