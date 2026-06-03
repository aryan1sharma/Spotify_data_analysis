"""Pure, testable logic layer for the GUI — no Tkinter, no I/O side-effects."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


def discover_csvs(data_dir: Path) -> list[Path]:
    """Return sorted CSVs in *data_dir*; empty list if dir missing or contains none."""
    if not data_dir.is_dir():
        return []
    return sorted(data_dir.glob("*.csv"))


def build_fetcher_command(
    playlist_ids: list[str],
    market: str,
    formats: list[str],
) -> list[str] | None:
    """Build the subprocess command for spotify_fetcher.py.

    Returns None if all playlist IDs are blank.
    -u is required: without it Python block-buffers piped stdout and lines
    only reach the GUI after the whole fetch finishes.
    """
    clean_ids = [pid.strip() for pid in playlist_ids if pid.strip()]
    if not clean_ids:
        return None

    fetcher_script = Path(__file__).parent.parent / "fetcher" / "spotify_fetcher.py"
    cmd: list[str] = [sys.executable, "-u", str(fetcher_script), "--playlist"] + clean_ids

    if market.strip():
        cmd += ["--market", market.strip()]
    if formats:
        cmd += ["--format"] + list(formats)

    return cmd


def build_filter_dict(form_state: dict) -> dict:
    """Convert form fields to an apply_filters-compatible dict.

    Blank/whitespace → None. "artists" and "artist_ids" are comma-split into
    lists (single value → one-item list). All other values are passed through stripped.
    """
    result: dict = {}
    list_fields = {"artists", "artist_ids"}
    for key, raw in form_state.items():
        stripped = raw.strip() if isinstance(raw, str) else raw
        if not stripped:
            result[key] = None
        elif key in list_fields:
            result[key] = [part.strip() for part in str(stripped).split(",")]
        else:
            result[key] = stripped
    return result


def dataframe_to_treeview_rows(df: pd.DataFrame) -> tuple[list[str], list[tuple]]:
    """Return (column_names, rows) for a ttk.Treeview. NaN → ""; all values str."""
    columns = list(df.columns)
    rows = [
        tuple("" if pd.isna(v) else str(v) for v in record)
        for record in df.itertuples(index=False, name=None)
    ]
    return columns, rows
