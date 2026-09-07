from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

import pandas as pd

from utils.data_loader import (
    DATABASE_COLUMNS,
    DataValidationError,
    _read_csv_compatible,
    clean_earthquake_data,
)
from utils.fault_data import FAULT_COLUMNS, enrich_earthquakes_with_faults
from utils.region_data import enrich_earthquakes_with_regions


STORE_SCHEMA_VERSION = "earthquake-store-v1"
REGION_COLUMNS = [
    "provinsi",
    "kabupaten_kota",
    "jenis_lokasi",
    "jarak_darat_km",
]
STORE_COLUMNS = [
    "waktu",
    "tanggal",
    "jam",
    "lintang",
    "bujur",
    "kedalaman",
    "magnitudo",
    "keterangan_lokasi",
    "dirasakan",
    *REGION_COLUMNS,
    *FAULT_COLUMNS,
]
CORE_EVENT_COLUMNS = [
    "waktu",
    "lintang",
    "bujur",
    "kedalaman",
    "magnitudo",
]
ProgressCallback = Callable[[str], None]


class EarthquakeStoreError(RuntimeError):
    """Raised when the SQLite working catalog cannot be built or queried."""


def _file_fingerprint(path: Path) -> dict[str, object]:
    resolved = path.resolve()
    if not resolved.exists():
        return {"name": resolved.name, "missing": True}
    stat = resolved.stat()
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return {
        "name": resolved.name,
        "size": stat.st_size,
        "sha256": digest.hexdigest(),
    }


def source_signature(
    archive_paths: Sequence[Path],
    reference_paths: Sequence[Path],
) -> str:
    payload = {
        "schema": STORE_SCHEMA_VERSION,
        "archives": [_file_fingerprint(path) for path in archive_paths],
        "references": [_file_fingerprint(path) for path in reference_paths],
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def working_database_signature(database_path: Path) -> tuple[int, int]:
    if not database_path.exists():
        return (0, 0)
    stat = database_path.stat()
    return (stat.st_mtime_ns, stat.st_size)


def _read_metadata(database_path: Path) -> dict[str, str]:
    if not database_path.exists():
        return {}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(database_path)
        rows = connection.execute("SELECT key, value FROM metadata").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        if connection is not None:
            connection.close()
    return {str(key): str(value) for key, value in rows}


def store_is_current(database_path: Path, signature: str) -> bool:
    metadata = _read_metadata(database_path)
    return (
        metadata.get("schema_version") == STORE_SCHEMA_VERSION
        and metadata.get("source_signature") == signature
    )


def _load_archives(archive_paths: Sequence[Path]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in archive_paths:
        if not path.exists():
            continue
        try:
            frame = _read_csv_compatible(path)
        except (OSError, UnicodeError, pd.errors.ParserError) as exc:
            raise EarthquakeStoreError(
                f"Arsip CSV tidak dapat dibaca: {path.name}"
            ) from exc
        if not set(DATABASE_COLUMNS[:6]).issubset(frame.columns):
            raise EarthquakeStoreError(
                f"Arsip {path.name} tidak memiliki kolom gempa wajib."
            )
        frames.append(frame)
    if not frames:
        raise EarthquakeStoreError("Tidak ada arsip CSV gempa yang dapat dimuat.")
    return pd.concat(frames, ignore_index=True, sort=False)


def _prepare_store_frame(
    archive_paths: Sequence[Path],
    fault_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
    district_geojson: dict[str, Any],
    progress: ProgressCallback | None,
) -> pd.DataFrame:
    if progress:
        progress("Membaca dan membersihkan arsip CSV...")
    raw = _load_archives(archive_paths)
    try:
        cleaned = clean_earthquake_data(raw)
    except DataValidationError as exc:
        raise EarthquakeStoreError(str(exc)) from exc
    if cleaned.empty:
        raise EarthquakeStoreError("Arsip CSV tidak menghasilkan data gempa valid.")

    # The application archive rounds imported observations while the original
    # BMKG catalog retains sub-second time and extra decimal places. Build the
    # identity at the canonical application precision so those two legitimate
    # representations do not become duplicate working events.
    identity = pd.DataFrame(
        {
            "waktu": cleaned["waktu"].dt.floor("s"),
            "lintang": cleaned["lintang"].round(5),
            "bujur": cleaned["bujur"].round(5),
            "kedalaman": cleaned["kedalaman"].round(1),
            "magnitudo": cleaned["magnitudo"].round(1),
        }
    )
    cleaned = cleaned.loc[
        ~identity.duplicated(subset=CORE_EVENT_COLUMNS, keep="last")
    ].reset_index(drop=True)
    if progress:
        progress(f"Menghitung sesar terdekat untuk {len(cleaned):,} kejadian...")
    enriched = enrich_earthquakes_with_faults(cleaned, fault_geojson)
    if progress:
        progress("Mengklasifikasikan provinsi, kabupaten/kota, dan darat/laut...")
    enriched = enrich_earthquakes_with_regions(
        enriched,
        province_geojson,
        district_geojson,
    )

    for column in STORE_COLUMNS:
        if column not in enriched.columns:
            enriched[column] = ""
    stored = enriched[STORE_COLUMNS].copy()
    stored["waktu"] = pd.to_datetime(stored["waktu"]).dt.strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )
    stored["tanggal"] = pd.to_datetime(enriched["waktu"]).dt.strftime("%Y-%m-%d")
    stored["jam"] = pd.to_datetime(enriched["waktu"]).dt.strftime("%H:%M:%S")
    return stored


def _write_database(
    database_path: Path,
    frame: pd.DataFrame,
    signature: str,
) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=database_path.parent,
            prefix=f".{database_path.stem}-",
            suffix=".sqlite.tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        connection = sqlite3.connect(temporary_path)
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            frame.to_sql(
                "earthquakes",
                connection,
                if_exists="replace",
                index=False,
                chunksize=5_000,
            )
            connection.executescript(
                """
                CREATE INDEX idx_earthquakes_waktu
                    ON earthquakes(waktu);
                CREATE INDEX idx_earthquakes_province_time
                    ON earthquakes(provinsi, waktu);
                CREATE INDEX idx_earthquakes_district_time
                    ON earthquakes(kabupaten_kota, waktu);
                CREATE INDEX idx_earthquakes_location_time
                    ON earthquakes(jenis_lokasi, waktu);
                CREATE INDEX idx_earthquakes_magnitude
                    ON earthquakes(magnitudo);
                CREATE TABLE metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            metadata = {
                "schema_version": STORE_SCHEMA_VERSION,
                "source_signature": signature,
                "row_count": str(len(frame)),
                "minimum_time": str(frame["waktu"].min()),
                "maximum_time": str(frame["waktu"].max()),
            }
            connection.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                metadata.items(),
            )
            connection.commit()
        finally:
            connection.close()
        os.replace(temporary_path, database_path)
        temporary_path = None
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise EarthquakeStoreError(
            "Database kerja SQLite tidak dapat ditulis dengan aman."
        ) from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def ensure_working_database(
    database_path: Path,
    archive_paths: Sequence[Path],
    reference_paths: Sequence[Path],
    fault_geojson: dict[str, Any],
    province_geojson: dict[str, Any],
    district_geojson: dict[str, Any],
    progress: ProgressCallback | None = None,
) -> bool:
    """Create/update the working database; return true when it was rebuilt."""
    signature = source_signature(archive_paths, reference_paths)
    if store_is_current(database_path, signature):
        return False
    frame = _prepare_store_frame(
        archive_paths,
        fault_geojson,
        province_geojson,
        district_geojson,
        progress,
    )
    if progress:
        progress("Menulis indeks kerja SQLite...")
    _write_database(database_path, frame, signature)
    return True


def get_store_summary(database_path: Path) -> dict[str, object]:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database_path.resolve()}?mode=ro",
            uri=True,
        )
        row = connection.execute(
            """
            SELECT COUNT(*), MIN(waktu), MAX(waktu),
                   MIN(kedalaman), MAX(kedalaman),
                   MIN(magnitudo), MAX(magnitudo)
            FROM earthquakes
            """
        ).fetchone()
    except sqlite3.Error as exc:
        raise EarthquakeStoreError("Ringkasan katalog SQLite tidak dapat dibaca.") from exc
    finally:
        if connection is not None:
            connection.close()
    if row is None or not row[0]:
        raise EarthquakeStoreError("Database kerja SQLite kosong.")
    return {
        "row_count": int(row[0]),
        "minimum_time": pd.Timestamp(row[1]),
        "maximum_time": pd.Timestamp(row[2]),
        "minimum_depth": float(row[3]),
        "maximum_depth": float(row[4]),
        "minimum_magnitude": float(row[5]),
        "maximum_magnitude": float(row[6]),
    }


def default_query_dates(summary: dict[str, object], days: int = 90) -> tuple[date, date]:
    maximum = pd.Timestamp(summary["maximum_time"]).date()
    minimum = pd.Timestamp(summary["minimum_time"]).date()
    return max(minimum, maximum - timedelta(days=max(1, days) - 1)), maximum


def query_earthquakes(
    database_path: Path,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    start = pd.Timestamp(start_date).strftime("%Y-%m-%d 00:00:00.000000")
    exclusive_end = (pd.Timestamp(end_date) + pd.Timedelta(days=1)).strftime(
        "%Y-%m-%d 00:00:00.000000"
    )
    sql = (
        "SELECT "
        + ", ".join(f'"{column}"' for column in STORE_COLUMNS)
        + " FROM earthquakes WHERE waktu >= ? AND waktu < ? ORDER BY waktu"
    )
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"file:{database_path.resolve()}?mode=ro",
            uri=True,
        )
        frame = pd.read_sql_query(
            sql,
            connection,
            params=(start, exclusive_end),
            parse_dates=["waktu"],
        )
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise EarthquakeStoreError("Query katalog SQLite gagal dijalankan.") from exc
    finally:
        if connection is not None:
            connection.close()
    return frame
