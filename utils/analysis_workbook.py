"""One Excel workbook per analysis, one sheet per step (the CSVs stay as well)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

MAX_SHEET_NAME = 31  # Excel limit
MAX_COLUMN_WIDTH = 60


def write_workbook(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    """Write ``sheets`` (name -> table) to ``path`` with a frozen header row.

    Column widths follow the longest value (capped). Needs openpyxl.
    """
    names = [name[:MAX_SHEET_NAME] for name in sheets]
    if len(set(names)) != len(names):
        raise ValueError("Nama sheet harus unik dalam 31 karakter pertama.")
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in zip(names, sheets.values()):
            frame.to_excel(writer, sheet_name=name, index=False)
            sheet = writer.sheets[name]
            sheet.freeze_panes = "A2"
            for idx, column in enumerate(frame.columns, start=1):
                values = [str(column)] + [str(v) for v in frame[column].head(500)]
                width = min(MAX_COLUMN_WIDTH, max(len(v) for v in values) + 2)
                sheet.column_dimensions[sheet.cell(row=1, column=idx).column_letter].width = width
