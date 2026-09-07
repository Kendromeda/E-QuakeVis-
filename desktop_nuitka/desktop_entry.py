from __future__ import annotations

import os
from functools import lru_cache
import hashlib
from pathlib import Path
import shutil
from types import ModuleType


APP_NAME = "E-QuakeVis"
LEGACY_APP_NAME = "DatabaseInteraktif"

# Streamlit's CachedFunc hashes Python function internals that Nuitka replaces
# during compilation.  Hashing a compiled function can terminate the MinGW
# standalone process, so desktop builds use a small in-process cache for the
# loaders whose arguments are immutable path/signature values.
LRU_CACHE_FUNCTIONS = {
    "load_database_file",
    "load_working_store_summary",
    "load_working_store_query",
    "load_population_density_map_reference",
    "load_fault_reference",
    "load_region_reference",
    "load_trench_reference",
    "load_slab_reference",
    "load_fault_province_reference",
}


def get_user_data_dir() -> Path:
    """Return a writable, per-user directory for persistent application data."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def migrate_legacy_user_data() -> None:
    """Move a previous installation's data forward without removing the original."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return
    destination_data = Path(local_app_data) / APP_NAME / "data"
    legacy_data = Path(local_app_data) / LEGACY_APP_NAME / "data"
    if not destination_data.exists() and legacy_data.exists():
        shutil.copytree(legacy_data, destination_data)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_before_seed_reset(path: Path, bundled_digest: str) -> None:
    """Keep one recoverable copy before replacing data from a new bundle."""
    if not path.exists():
        return
    backup_dir = path.parent / "seed_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / (
        f"{path.stem}_before_{bundled_digest[:12]}{path.suffix}"
    )
    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def configure_persistent_database(app_module: ModuleType) -> Path:
    """Seed writable CSV/SQLite working data outside the bundled application."""
    migrate_legacy_user_data()
    bundled_master = Path(app_module.APP_DIR) / "data" / "gempa_master.csv"
    bundled_working = Path(app_module.APP_DIR) / "data" / "gempa_working.sqlite"
    user_database = get_user_data_dir() / "data" / "gempa_master.csv"
    user_working = get_user_data_dir() / "data" / "gempa_working.sqlite"
    seed_marker = get_user_data_dir() / "data" / "bundled_master.sha256"
    user_database.parent.mkdir(parents=True, exist_ok=True)

    app_module.SEED_DATA_PATH = bundled_master if bundled_master.exists() else None
    if bundled_master.exists():
        bundled_digest = _sha256(bundled_master)
        installed_digest = (
            seed_marker.read_text(encoding="ascii").strip()
            if seed_marker.exists()
            else ""
        )
        seed_changed = installed_digest != bundled_digest
        if not user_database.exists():
            shutil.copy2(bundled_master, user_database)
        elif seed_changed:
            _backup_before_seed_reset(user_database, bundled_digest)
            shutil.copy2(bundled_master, user_database)
        if bundled_working.exists() and (
            not user_working.exists() or seed_changed
        ):
            if seed_changed:
                _backup_before_seed_reset(user_working, bundled_digest)
            shutil.copy2(bundled_working, user_working)
        seed_marker.write_text(bundled_digest, encoding="ascii")
    elif not user_working.exists() and bundled_working.exists():
        shutil.copy2(bundled_working, user_working)
    app_module.MASTER_DATA_PATH = user_database
    app_module.WORKING_DATABASE_PATH = user_working
    return user_database


def configure_compiled_caches(app_module: ModuleType) -> None:
    """Replace Streamlit caches that cannot safely hash Nuitka functions."""
    for name, value in list(vars(app_module).items()):
        if type(value).__name__ != "CachedFunc" or not hasattr(value, "__wrapped__"):
            continue

        original = value.__wrapped__
        if name in LRU_CACHE_FUNCTIONS:
            cached = lru_cache(maxsize=16)(original)
            # Keep the API used after importing new earthquake data.
            cached.clear = cached.cache_clear
            setattr(app_module, name, cached)
        else:
            setattr(app_module, name, original)


def main() -> None:
    # Importing inside Streamlit's script context keeps set_page_config and all
    # other Streamlit calls attached to the correct session.
    import app as app_module

    configure_persistent_database(app_module)
    configure_compiled_caches(app_module)
    app_module.main()


if __name__ == "__main__":
    main()
