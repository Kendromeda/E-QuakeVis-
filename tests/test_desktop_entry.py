from __future__ import annotations

import hashlib
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

from desktop_nuitka.desktop_entry import configure_persistent_database


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class DesktopEntryTest(unittest.TestCase):
    def _make_app(self, app_dir: Path) -> ModuleType:
        app = ModuleType("app")
        app.APP_DIR = app_dir
        app.MASTER_DATA_PATH = app_dir / "unused.csv"
        app.WORKING_DATABASE_PATH = app_dir / "unused.sqlite"
        app.SEED_DATA_PATH = None
        return app

    def test_new_bundle_replaces_active_data_and_keeps_backups(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir = root / "bundle"
            bundled_data = app_dir / "data"
            bundled_data.mkdir(parents=True)
            bundled_master = bundled_data / "gempa_master.csv"
            bundled_working = bundled_data / "gempa_working.sqlite"
            bundled_master.write_text("tanggal\n2025-01-01\n", encoding="utf-8")
            bundled_working.write_bytes(b"new sqlite")

            local_app_data = root / "local"
            user_data = local_app_data / "E-QuakeVis" / "data"
            user_data.mkdir(parents=True)
            user_master = user_data / "gempa_master.csv"
            user_working = user_data / "gempa_working.sqlite"
            user_master.write_text("tanggal\n2024-01-01\n", encoding="utf-8")
            user_working.write_bytes(b"old sqlite")
            (user_data / "bundled_master.sha256").write_text(
                "old-digest", encoding="ascii"
            )

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}):
                app = self._make_app(app_dir)
                configured = configure_persistent_database(app)

            digest = _sha256(bundled_master)
            self.assertEqual(configured.read_bytes(), bundled_master.read_bytes())
            self.assertEqual(user_working.read_bytes(), bundled_working.read_bytes())
            self.assertEqual(
                (user_data / "bundled_master.sha256").read_text(encoding="ascii"),
                digest,
            )
            backup_dir = user_data / "seed_backups"
            self.assertEqual(
                (backup_dir / f"gempa_master_before_{digest[:12]}.csv").read_text(
                    encoding="utf-8"
                ),
                "tanggal\n2024-01-01\n",
            )
            self.assertEqual(
                (
                    backup_dir
                    / f"gempa_working_before_{digest[:12]}.sqlite"
                ).read_bytes(),
                b"old sqlite",
            )
            self.assertEqual(app.MASTER_DATA_PATH, user_master)
            self.assertEqual(app.WORKING_DATABASE_PATH, user_working)

    def test_unchanged_bundle_preserves_active_user_data(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir = root / "bundle"
            bundled_data = app_dir / "data"
            bundled_data.mkdir(parents=True)
            bundled_master = bundled_data / "gempa_master.csv"
            bundled_working = bundled_data / "gempa_working.sqlite"
            bundled_master.write_text("tanggal\n2025-01-01\n", encoding="utf-8")
            bundled_working.write_bytes(b"bundled sqlite")

            local_app_data = root / "local"
            user_data = local_app_data / "E-QuakeVis" / "data"
            user_data.mkdir(parents=True)
            user_master = user_data / "gempa_master.csv"
            user_working = user_data / "gempa_working.sqlite"
            user_master.write_text(
                "tanggal\n2025-01-01\n2025-02-01\n", encoding="utf-8"
            )
            user_working.write_bytes(b"user sqlite")
            (user_data / "bundled_master.sha256").write_text(
                _sha256(bundled_master), encoding="ascii"
            )

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}):
                configure_persistent_database(self._make_app(app_dir))

            self.assertEqual(
                user_master.read_text(encoding="utf-8"),
                "tanggal\n2025-01-01\n2025-02-01\n",
            )
            self.assertEqual(user_working.read_bytes(), b"user sqlite")
            self.assertFalse((user_data / "seed_backups").exists())

    def test_legacy_installation_is_copied_to_new_application_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            app_dir = root / "bundle"
            bundled_data = app_dir / "data"
            bundled_data.mkdir(parents=True)
            bundled_master = bundled_data / "gempa_master.csv"
            bundled_working = bundled_data / "gempa_working.sqlite"
            bundled_master.write_text("tanggal\n2025-01-01\n", encoding="utf-8")
            bundled_working.write_bytes(b"bundled sqlite")

            local_app_data = root / "local"
            legacy_data = local_app_data / "DatabaseInteraktif" / "data"
            legacy_data.mkdir(parents=True)
            (legacy_data / "gempa_master.csv").write_text(
                "tanggal\n2025-01-01\n2025-02-01\n", encoding="utf-8"
            )
            (legacy_data / "gempa_working.sqlite").write_bytes(b"legacy sqlite")
            (legacy_data / "bundled_master.sha256").write_text(
                _sha256(bundled_master), encoding="ascii"
            )
            # The launcher creates this directory before desktop_entry runs.
            (local_app_data / "E-QuakeVis" / "runtime").mkdir(parents=True)

            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app_data)}):
                app = self._make_app(app_dir)
                configured = configure_persistent_database(app)

            new_data = local_app_data / "E-QuakeVis" / "data"
            self.assertEqual(configured, new_data / "gempa_master.csv")
            self.assertEqual(
                configured.read_text(encoding="utf-8"),
                "tanggal\n2025-01-01\n2025-02-01\n",
            )
            self.assertEqual(
                (new_data / "gempa_working.sqlite").read_bytes(), b"legacy sqlite"
            )
            self.assertTrue((legacy_data / "gempa_master.csv").exists())


if __name__ == "__main__":
    unittest.main()
