from __future__ import annotations

from datetime import datetime, time
from io import BytesIO
from pathlib import Path
import os
import re
import tempfile
from typing import Any, BinaryIO, Collection, Mapping, Sequence

import pandas as pd


# Mirrors utils.region_data.SEA_LOCATION. ``jenis_lokasi`` is derived from the
# province GeoJSON after loading, so it is deliberately not stored in the CSV.
# The local literal keeps filtering independent from the region module.
SEA_LOCATION = "Laut"

REQUIRED_COLUMNS = ["tanggal", "jam", "lintang", "bujur", "kedalaman", "magnitudo"]
OPTIONAL_COLUMNS = ["keterangan_lokasi", "dirasakan"]
DATABASE_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
INDONESIA_LAT_RANGE = (-11.0, 6.0)
INDONESIA_LON_RANGE = (95.0, 141.0)
HEADER_SCAN_ROWS = 20

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "tanggal": ("tanggal wib", "date", "tanggal utc"),
    "jam": ("waktu wib", "jam wib", "waktu", "time", "waktu utc", "jam utc"),
    "lintang": ("latitude", "lat", "episenter lintang"),
    "bujur": ("longitude", "lon", "long", "episenter bujur"),
    "kedalaman": ("kedalaman km", "depth", "depth km"),
    "magnitudo": ("magnitude", "mag", "m"),
    "keterangan_lokasi": ("lokasi", "location", "keterangan lokasi"),
    "dirasakan": ("felt", "keterangan dirasakan", "keterangan"),
}

COLUMN_LABELS = {
    "tanggal": "Tanggal",
    "jam": "Jam/Waktu",
    "lintang": "Lintang",
    "bujur": "Bujur",
    "kedalaman": "Kedalaman",
    "magnitudo": "Magnitudo",
    "keterangan_lokasi": "Keterangan lokasi",
    "dirasakan": "Informasi dirasakan",
}


class DataValidationError(ValueError):
    """Raised when uploaded earthquake data does not match the required schema."""


class UnsupportedFileTypeError(ValueError):
    """Raised when the uploaded file type is not supported."""


class DatabaseWriteError(OSError):
    """Raised when the persistent master database cannot be replaced safely."""


def _read_csv_compatible(source: Any, *, nrows: int | None = None) -> pd.DataFrame:
    """Read UTF-8 CSV files while accepting older Windows-encoded exports."""
    try:
        # The pure-Python parser is compatible with Nuitka's MinGW standalone
        # build, where pandas' optional C parser can abort the whole process.
        dataframe = pd.read_csv(
            source, encoding="utf-8-sig", engine="python", nrows=nrows
        )
        dataframe.attrs["source_encoding"] = "utf-8"
        return dataframe
    except UnicodeDecodeError:
        if hasattr(source, "seek"):
            source.seek(0)
        dataframe = pd.read_csv(source, encoding="cp1252", engine="python", nrows=nrows)
        dataframe.attrs["source_encoding"] = "cp1252"
        return dataframe


def _uploaded_bytes(uploaded_file: BinaryIO) -> bytes:
    if hasattr(uploaded_file, "getvalue"):
        return bytes(uploaded_file.getvalue())
    position = uploaded_file.tell() if hasattr(uploaded_file, "tell") else None
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    content = uploaded_file.read()
    if position is not None and hasattr(uploaded_file, "seek"):
        uploaded_file.seek(position)
    return bytes(content)


def _normalized_header(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip().lower().replace("\n", " ")
    text = re.sub(r"\([^)]*\)", lambda match: f" {match.group(0)[1:-1]} ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def has_canonical_columns(columns: Sequence[object]) -> bool:
    """Return true when required database headers already exist verbatim."""
    names = {str(column).strip() for column in columns}
    return all(column in names for column in REQUIRED_COLUMNS)


def suggest_column_mapping(columns: Sequence[object]) -> dict[str, str | None]:
    """Suggest database fields for human-readable source column names."""
    source_columns = [str(column) for column in columns]
    normalized_sources = {
        column: _normalized_header(column) for column in source_columns
    }
    mapping: dict[str, str | None] = {}
    used_sources: set[str] = set()

    for target in DATABASE_COLUMNS:
        target_name = _normalized_header(target)
        aliases = (target_name, *COLUMN_ALIASES.get(target, ()))
        best_source: str | None = None
        best_score = -1
        for source in source_columns:
            if source in used_sources:
                continue
            normalized = normalized_sources[source]
            if not normalized:
                continue
            score = -1
            if normalized == target_name:
                score = 1_000
            else:
                for alias_index, alias in enumerate(aliases[1:]):
                    alias_name = _normalized_header(alias)
                    if normalized == alias_name:
                        score = max(score, 700 - alias_index)
                    elif alias_name and re.search(
                        rf"(?:^| ){re.escape(alias_name)}(?: |$)",
                        normalized,
                    ):
                        score = max(score, 400 - alias_index)
            if score > best_score:
                best_source = source
                best_score = score
        mapping[target] = best_source if best_score >= 0 else None
        if mapping[target] is not None:
            used_sources.add(mapping[target])
    return mapping


def _uses_secondary_header(primary: pd.Series, secondary: pd.Series) -> bool:
    del primary
    recognized = {
        _normalized_header(alias)
        for target, aliases in COLUMN_ALIASES.items()
        for alias in (target, *aliases)
    }
    recognized.update({"utc", "wib", "lintang", "bujur", "lokasi", "dirasakan"})
    return sum(_normalized_header(value) in recognized for value in secondary) >= 2


def _unique_column_names(names: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    unique: list[str] = []
    for name in names:
        base = name or "Kolom tanpa judul"
        counts[base] = counts.get(base, 0) + 1
        unique.append(base if counts[base] == 1 else f"{base} [{counts[base]}]")
    return unique


def _header_names(
    raw_df: pd.DataFrame,
    header_row: int,
) -> tuple[list[str], bool]:
    primary = raw_df.iloc[header_row]
    secondary = (
        raw_df.iloc[header_row + 1]
        if header_row + 1 < len(raw_df)
        else pd.Series([None] * len(primary))
    )
    uses_secondary = _uses_secondary_header(primary, secondary)
    names: list[str] = []
    last_parent = ""
    for column_index, primary_value in enumerate(primary):
        parent = "" if pd.isna(primary_value) else str(primary_value).strip()
        child_value = secondary.iloc[column_index]
        child = "" if pd.isna(child_value) else str(child_value).strip()
        if parent:
            last_parent = parent
        elif uses_secondary and child:
            parent = last_parent
        if uses_secondary and child:
            normalized_parent = _normalized_header(parent)
            normalized_child = _normalized_header(child)
            name = child if not parent else (
                parent if normalized_parent == normalized_child else f"{parent} / {child}"
            )
        else:
            name = parent
        names.append(name or f"Kolom {column_index + 1}")
    return _unique_column_names(names), uses_secondary


def detect_header_row(raw_df: pd.DataFrame) -> int:
    """Find the most likely first header row in a raw spreadsheet sheet."""
    if raw_df.empty:
        return 0
    best_row = 0
    best_score = -1.0
    for row_index in range(min(HEADER_SCAN_ROWS, len(raw_df))):
        names, _ = _header_names(raw_df, row_index)
        mapping = suggest_column_mapping(names)
        required_matches = sum(mapping[column] is not None for column in REQUIRED_COLUMNS)
        optional_matches = sum(mapping[column] is not None for column in OPTIONAL_COLUMNS)
        score = required_matches * 100 + optional_matches * 10 - row_index * 0.01
        if score > best_score:
            best_row = row_index
            best_score = score
    return best_row


def _table_from_raw(raw_df: pd.DataFrame, header_row: int) -> pd.DataFrame:
    if raw_df.empty:
        return raw_df.copy()
    header_row = max(0, min(int(header_row), len(raw_df) - 1))
    column_names, uses_secondary = _header_names(raw_df, header_row)
    data_start = header_row + (2 if uses_secondary else 1)
    table = raw_df.iloc[data_start:].copy()
    table.columns = column_names
    table = table.dropna(how="all").reset_index(drop=True)
    return table


def inspect_uploaded_file(uploaded_file: BinaryIO) -> dict[str, Any]:
    """Inspect sheets and likely headers without modifying the uploaded file."""
    filename = getattr(uploaded_file, "name", "")
    suffix = Path(filename).suffix.lower()
    content = _uploaded_bytes(uploaded_file)
    if suffix == ".csv":
        try:
            table = _read_csv_compatible(BytesIO(content))
        except Exception as exc:
            raise UnsupportedFileTypeError(
                "File tidak bisa dibaca, pastikan format CSV atau Excel yang valid."
            ) from exc
        mapping = suggest_column_mapping(table.columns)
        return {
            "is_excel": False,
            "sheets": [],
            "suggested_sheet": None,
            "canonical": has_canonical_columns(table.columns),
            "required_matches": sum(
                mapping[column] is not None for column in REQUIRED_COLUMNS
            ),
        }
    if suffix not in {".xlsx", ".xls"}:
        raise UnsupportedFileTypeError(
            "File tidak bisa dibaca, pastikan format CSV atau Excel yang valid."
        )
    try:
        excel = pd.ExcelFile(BytesIO(content))
        sheet_details: list[dict[str, Any]] = []
        for sheet_name in excel.sheet_names:
            preview = pd.read_excel(
                excel,
                sheet_name=sheet_name,
                header=None,
                nrows=HEADER_SCAN_ROWS + 2,
            )
            header_row = detect_header_row(preview)
            table = _table_from_raw(preview, header_row)
            mapping = suggest_column_mapping(table.columns)
            required_matches = sum(
                mapping[column] is not None for column in REQUIRED_COLUMNS
            )
            optional_matches = sum(
                mapping[column] is not None for column in OPTIONAL_COLUMNS
            )
            sheet_details.append(
                {
                    "name": sheet_name,
                    "header_row": header_row,
                    "required_matches": required_matches,
                    "optional_matches": optional_matches,
                    "canonical": has_canonical_columns(table.columns),
                    "score": required_matches * 100 + optional_matches * 10,
                }
            )
    except Exception as exc:
        raise UnsupportedFileTypeError(
            "File tidak bisa dibaca, pastikan format CSV atau Excel yang valid."
        ) from exc
    suggested = max(sheet_details, key=lambda item: item["score"], default=None)
    return {
        "is_excel": True,
        "sheets": sheet_details,
        "suggested_sheet": suggested["name"] if suggested else None,
        "canonical": bool(suggested and suggested["canonical"]),
        "required_matches": suggested["required_matches"] if suggested else 0,
    }


def load_uploaded_file(
    uploaded_file: BinaryIO,
    *,
    sheet_name: str | None = None,
    header_row: int | None = None,
    max_rows: int | None = None,
) -> pd.DataFrame:
    """Load a CSV or a selected Excel sheet into a table."""
    filename = getattr(uploaded_file, "name", "")
    suffix = Path(filename).suffix.lower()
    content = _uploaded_bytes(uploaded_file)

    try:
        if suffix == ".csv":
            return _read_csv_compatible(BytesIO(content), nrows=max_rows)
        if suffix in {".xlsx", ".xls"}:
            excel = pd.ExcelFile(BytesIO(content))
            selected_sheet = sheet_name or excel.sheet_names[0]
            if header_row is None:
                preview = pd.read_excel(
                    excel,
                    sheet_name=selected_sheet,
                    header=None,
                    nrows=HEADER_SCAN_ROWS + 2,
                )
                selected_header = detect_header_row(preview)
            else:
                selected_header = int(header_row)
            raw_row_limit = (
                None
                if max_rows is None
                else max(1, selected_header + int(max_rows) + 2)
            )
            raw_df = pd.read_excel(
                excel,
                sheet_name=selected_sheet,
                header=None,
                nrows=raw_row_limit,
            )
            return _table_from_raw(raw_df, selected_header)
    except Exception as exc:  # pandas/openpyxl surface several parser-specific exceptions.
        raise UnsupportedFileTypeError(
            "File tidak bisa dibaca, pastikan format CSV atau Excel yang valid."
        ) from exc

    raise UnsupportedFileTypeError(
        "File tidak bisa dibaca, pastikan format CSV atau Excel yang valid."
    )


def apply_column_mapping(
    df: pd.DataFrame,
    mapping: Mapping[str, str | None],
) -> pd.DataFrame:
    """Select and rename uploaded columns to the canonical database schema."""
    missing = [column for column in REQUIRED_COLUMNS if not mapping.get(column)]
    if missing:
        labels = ", ".join(COLUMN_LABELS[column] for column in missing)
        raise DataValidationError(f"Lengkapi pemetaan kolom wajib: {labels}.")
    selected_sources = [source for source in mapping.values() if source]
    if len(selected_sources) != len(set(selected_sources)):
        raise DataValidationError("Satu kolom sumber tidak boleh dipakai untuk dua tujuan.")
    unavailable = [source for source in selected_sources if source not in df.columns]
    if unavailable:
        raise DataValidationError("Pemetaan kolom berubah. Pilih kembali kolom sumber.")

    mapped = pd.DataFrame(index=df.index)
    for target in DATABASE_COLUMNS:
        source = mapping.get(target)
        if source:
            mapped[target] = df[source]
    meaningful = mapped.apply(
        lambda column: column.notna() & column.astype(str).str.strip().ne("")
    ).any(axis=1)
    return mapped.loc[meaningful].reset_index(drop=True)


def _time_to_seconds(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return (
            value.hour * 3600
            + value.minute * 60
            + value.second
            + value.microsecond / 1_000_000
        )
    if isinstance(value, (int, float)):
        return float(value % 1) * 86400
    parsed = pd.to_datetime(str(value).strip(), errors="coerce")
    if pd.isna(parsed):
        return None
    return (
        parsed.hour * 3600
        + parsed.minute * 60
        + parsed.second
        + parsed.microsecond / 1_000_000
    )


def _time_series_to_seconds(values: pd.Series) -> pd.Series:
    """Vectorize common clock/Excel values and fall back only for odd cells."""
    text = values.astype(str).str.strip()
    clock_parts = text.str.extract(
        r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?$"
    ).apply(pd.to_numeric, errors="coerce")
    seconds = pd.Series(float("nan"), index=values.index, dtype="float64")
    clock_mask = clock_parts[0].notna() & clock_parts[1].notna()
    seconds.loc[clock_mask] = (
        clock_parts.loc[clock_mask, 0] * 3600
        + clock_parts.loc[clock_mask, 1] * 60
        + clock_parts.loc[clock_mask, 2].fillna(0)
    )

    numeric = pd.to_numeric(values, errors="coerce")
    numeric_mask = seconds.isna() & numeric.notna()
    seconds.loc[numeric_mask] = (numeric.loc[numeric_mask] % 1) * 86400

    unresolved = seconds.isna() & values.notna()
    if unresolved.any():
        seconds.loc[unresolved] = values.loc[unresolved].map(_time_to_seconds)
    return seconds


def _parse_date_series(values: pd.Series) -> pd.Series:
    """Parse the application's common date layouts without per-row inference."""
    date_text = values.astype(str).str.strip()
    date_token = date_text.str.extract(
        r"^\s*((?:\d{4}[-/]\d{1,2}[-/]\d{1,2})|"
        r"(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4}))"
    )[0]
    normalized = date_token.str.replace("-", "/", regex=False)
    year_first = normalized.str.match(r"^\d{4}/", na=False)
    parsed_dates = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    parsed_dates.loc[year_first] = pd.to_datetime(
        normalized.loc[year_first],
        errors="coerce",
        format="%Y/%m/%d",
        cache=True,
    )

    non_year_mask = ~year_first & normalized.notna()
    numeric_date_parts = normalized.loc[non_year_mask].str.extract(
        r"^(\d{1,2})/(\d{1,2})/\d{2,4}$"
    ).apply(pd.to_numeric, errors="coerce")
    month_first_evidence = int(
        (numeric_date_parts[0].le(12) & numeric_date_parts[1].gt(12)).sum()
    )
    day_first_evidence = int(
        (numeric_date_parts[0].gt(12) & numeric_date_parts[1].le(12)).sum()
    )
    day_first = day_first_evidence >= month_first_evidence
    four_digit_year = normalized.loc[non_year_mask].str.match(
        r"^\d{1,2}/\d{1,2}/\d{4}$",
        na=False,
    )
    four_digit_indexes = four_digit_year[four_digit_year].index
    parsed_dates.loc[four_digit_indexes] = pd.to_datetime(
        normalized.loc[four_digit_indexes],
        errors="coerce",
        format="%d/%m/%Y" if day_first else "%m/%d/%Y",
        cache=True,
    )

    unresolved = parsed_dates.isna() & values.notna()
    if unresolved.any():
        parsed_dates.loc[unresolved] = pd.to_datetime(
            values.loc[unresolved],
            errors="coerce",
            format="mixed",
            dayfirst=day_first,
        )
    return parsed_dates


def validate_required_columns(df: pd.DataFrame) -> None:
    """Validate that all required case-sensitive columns exist."""
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing_columns:
        missing = ", ".join(missing_columns)
        expected = ", ".join(REQUIRED_COLUMNS)
        raise DataValidationError(
            f"Kolom wajib hilang: {missing}. Format kolom harus persis: {expected}."
        )


def clean_earthquake_data(df: pd.DataFrame) -> pd.DataFrame:
    """Clean validated earthquake data and return rows ready for filtering."""
    validate_required_columns(df)

    cleaned = df.copy()
    cleaned["lintang"] = pd.to_numeric(cleaned["lintang"], errors="coerce")
    cleaned["bujur"] = pd.to_numeric(cleaned["bujur"], errors="coerce")
    cleaned["kedalaman"] = pd.to_numeric(cleaned["kedalaman"], errors="coerce")
    cleaned["magnitudo"] = pd.to_numeric(cleaned["magnitudo"], errors="coerce")
    parsed_dates = _parse_date_series(cleaned["tanggal"])
    time_seconds = _time_series_to_seconds(cleaned["jam"])
    cleaned["waktu"] = parsed_dates.dt.normalize() + pd.to_timedelta(
        time_seconds.fillna(0),
        unit="s",
    )
    cleaned.loc[time_seconds.isna(), "waktu"] = parsed_dates[time_seconds.isna()]

    cleaned = cleaned.dropna(
        subset=["lintang", "bujur", "kedalaman", "magnitudo", "waktu"]
    )
    cleaned = cleaned[
        cleaned["lintang"].between(*INDONESIA_LAT_RANGE)
        & cleaned["bujur"].between(*INDONESIA_LON_RANGE)
    ]

    # Stable ordering lets later duplicate rows supply corrected optional metadata.
    return cleaned.sort_values("waktu", kind="stable").reset_index(drop=True)


def normalize_for_database(df: pd.DataFrame) -> pd.DataFrame:
    """Return a canonical CSV-safe representation for the master database."""
    normalized = clean_earthquake_data(df)
    if normalized.empty:
        return pd.DataFrame(columns=DATABASE_COLUMNS)

    database_df = pd.DataFrame(
        {
            "tanggal": normalized["waktu"].dt.strftime("%Y-%m-%d"),
            "jam": normalized["waktu"].dt.strftime("%H:%M:%S"),
            "lintang": normalized["lintang"].round(5),
            "bujur": normalized["bujur"].round(5),
            "kedalaman": normalized["kedalaman"].round(1),
            "magnitudo": normalized["magnitudo"].round(1),
        }
    )
    for column in OPTIONAL_COLUMNS:
        if column in normalized.columns:
            database_df[column] = (
                normalized[column].fillna("").astype(str).str.strip()
            )
        else:
            database_df[column] = ""
    return database_df.drop_duplicates().reset_index(drop=True)


def deduplicate_database_records(df: pd.DataFrame) -> pd.DataFrame:
    """Merge canonical duplicate events without discarding optional metadata.

    Event identity follows the six required observation fields after canonical
    rounding.  For optional fields, the latest non-empty value wins so a repeated
    import can enrich an existing event instead of creating another row or erasing
    information already present.
    """
    database_df = normalize_for_database(df)
    if database_df.empty:
        return pd.DataFrame(columns=DATABASE_COLUMNS)

    def latest_nonempty(series: pd.Series) -> str:
        values = series.fillna("").astype(str).str.strip()
        nonempty = values[values.ne("")]
        return str(nonempty.iloc[-1]) if not nonempty.empty else ""

    merged = (
        database_df.groupby(
            REQUIRED_COLUMNS,
            sort=False,
            as_index=False,
            dropna=False,
        )
        .agg({column: latest_nonempty for column in OPTIONAL_COLUMNS})
    )
    return merged[DATABASE_COLUMNS].reset_index(drop=True)


def load_master_database(master_path: Path, seed_path: Path | None = None) -> pd.DataFrame:
    """Load and repair the master CSV, optionally creating it from a seed file."""
    if not master_path.exists():
        if seed_path is None or not seed_path.exists():
            return pd.DataFrame(columns=DATABASE_COLUMNS)

        seed_df = _read_csv_compatible(seed_path)
        save_master_database(master_path, seed_df)

    raw_df = _read_csv_compatible(master_path)
    normalized_df = normalize_for_database(raw_df)[DATABASE_COLUMNS].reset_index(
        drop=True
    )
    # A database written by this application is already deduplicated. Avoid the
    # expensive Python groupby aggregation on every read; only invoke the repair
    # path when two rows still describe the same canonical event.
    has_duplicate_events = bool(
        normalized_df.duplicated(subset=REQUIRED_COLUMNS, keep=False).any()
    )
    database_df = (
        deduplicate_database_records(normalized_df)
        if has_duplicate_events
        else normalized_df
    )
    needs_encoding_migration = raw_df.attrs.get("source_encoding") != "utf-8"
    needs_schema_migration = list(map(str, raw_df.columns)) != DATABASE_COLUMNS
    if (
        needs_encoding_migration
        or needs_schema_migration
        or not database_df.equals(normalized_df)
    ):
        try:
            _write_database_atomic(master_path, database_df)
        except DatabaseWriteError:
            # Keep the app usable with deduplicated in-memory data when Excel or
            # another process temporarily locks the CSV.  A later load retries.
            pass
    return database_df


def _write_database_atomic(master_path: Path, database_df: pd.DataFrame) -> None:
    """Replace the master CSV atomically, leaving the old file intact on failure."""
    master_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=master_path.parent,
            prefix=f".{master_path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            database_df.to_csv(temporary_file, index=False)
        os.replace(temporary_path, master_path)
    except PermissionError as exc:
        raise DatabaseWriteError(
            "Database sedang dibuka oleh aplikasi lain. Tutup file gempa_master.csv "
            "di Excel, lalu coba lagi."
        ) from exc
    except OSError as exc:
        raise DatabaseWriteError("Database tidak dapat disimpan dengan aman.") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def save_master_database(master_path: Path, df: pd.DataFrame) -> None:
    """Persist canonical earthquake rows to the master CSV."""
    database_df = deduplicate_database_records(df)
    _write_database_atomic(master_path, database_df)


def append_to_master_database(
    master_path: Path,
    upload_df: pd.DataFrame,
    seed_path: Path | None = None,
) -> dict[str, int]:
    """Append cleaned upload rows to the master CSV and deduplicate canonical records."""
    existing_df = load_master_database(master_path, seed_path)
    incoming_df = normalize_for_database(upload_df)

    before_count = len(existing_df)
    combined_df = pd.concat([existing_df, incoming_df], ignore_index=True)
    combined_df = deduplicate_database_records(combined_df)
    save_master_database(master_path, combined_df)

    after_count = len(combined_df)
    added_rows = after_count - before_count
    return {
        "received_rows": int(len(upload_df)),
        "valid_rows": int(len(incoming_df)),
        "added_rows": int(added_rows),
        "merged_rows": int(max(0, len(incoming_df) - added_rows)),
        "total_rows": int(after_count),
    }


def filter_earthquake_data(
    df: pd.DataFrame,
    depth_range: tuple[float, float],
    magnitude_range: tuple[float, float],
    date_range: tuple[pd.Timestamp, pd.Timestamp],
    province_names: Collection[str] | None = None,
    district_names: Collection[str] | None = None,
    location_types: Collection[str] | None = None,
    fault_distances: pd.Series | None = None,
    max_fault_distance: float | None = None,
    volcano_distances: pd.Series | None = None,
    max_volcano_distance: float | None = None,
    coast_distances: pd.Series | None = None,
    max_coast_distance: float | None = None,
) -> pd.DataFrame:
    """Apply earthquake filters, optionally by distance to mapped references.

    ``fault_distances`` is a per-row distance (km) to the selected fault(s), aligned to
    ``df``'s index; rows farther than ``max_fault_distance`` are dropped.

    ``volcano_distances`` is the great-circle surface distance (km) to the nearest
    selected volcano. Rows farther than ``max_volcano_distance`` are dropped.

    ``coast_distances`` is a per-row distance (km) to the coastline of the selected
    coastal kabupaten/kota. It widens rather than narrows the district selection:
    offshore events within ``max_coast_distance`` of that coast join the chosen
    districts' own on-land events, so a coastal region can be studied together with
    the seismicity off its shore.
    """
    start_date, end_date = date_range
    start_time = pd.Timestamp(start_date).normalize()
    end_time = pd.Timestamp(end_date).normalize() + pd.Timedelta(days=1) - pd.Timedelta(microseconds=1)

    filtered = df[
        df["kedalaman"].between(depth_range[0], depth_range[1])
        & df["magnitudo"].between(magnitude_range[0], magnitude_range[1])
        & df["waktu"].between(start_time, end_time)
    ]

    if fault_distances is not None and max_fault_distance is not None:
        aligned_distances = pd.to_numeric(
            fault_distances.reindex(filtered.index),
            errors="coerce",
        )
        filtered = filtered[aligned_distances.le(float(max_fault_distance))]

    if volcano_distances is not None and max_volcano_distance is not None:
        aligned_distances = pd.to_numeric(
            volcano_distances.reindex(filtered.index),
            errors="coerce",
        )
        filtered = filtered[aligned_distances.le(float(max_volcano_distance))]

    coast_active = coast_distances is not None and max_coast_distance is not None
    aligned_coast = (
        pd.to_numeric(coast_distances.reindex(filtered.index), errors="coerce")
        if coast_active
        else None
    )
    # The coastal filter only pulls in *offshore* events near the shore; a land
    # event that belongs to a different kabupaten/kota is never added just because
    # it happens to sit within the radius.
    offshore_near_coast = (
        (filtered["jenis_lokasi"] == SEA_LOCATION)
        & aligned_coast.le(float(max_coast_distance))
        if aligned_coast is not None and "jenis_lokasi" in filtered.columns
        else None
    )

    if district_names and "kabupaten_kota" in filtered.columns:
        district_mask = filtered["kabupaten_kota"].isin(district_names)
        if offshore_near_coast is not None:
            # Union: the selected districts' own events plus offshore events near
            # their shore -- but not other districts' land events.
            district_mask = district_mask | offshore_near_coast
        filtered = filtered[district_mask]
    elif offshore_near_coast is not None:
        filtered = filtered[offshore_near_coast]

    if province_names and "provinsi" in filtered.columns:
        filtered = filtered[filtered["provinsi"].isin(province_names)]

    if location_types and "jenis_lokasi" in filtered.columns:
        filtered = filtered[filtered["jenis_lokasi"].isin(location_types)]

    return filtered.reset_index(drop=True)
