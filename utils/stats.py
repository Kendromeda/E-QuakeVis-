from __future__ import annotations

import pandas as pd


def calculate_summary_stats(df: pd.DataFrame) -> dict[str, float | int | None]:
    """Return summary statistics for filtered earthquake rows."""
    if df.empty:
        return {
            "jumlah_gempa": 0,
            "magnitudo_rata_rata": None,
            "magnitudo_maksimum": None,
        }

    return {
        "jumlah_gempa": int(len(df)),
        "magnitudo_rata_rata": float(df["magnitudo"].mean()),
        "magnitudo_maksimum": float(df["magnitudo"].max()),
    }
