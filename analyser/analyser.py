#!/usr/bin/env python3
"""Analyse and filter Spotify playlist data produced by spotify_fetcher.

Loads CSV/JSON/Parquet files (or a directory of them); returns every track
satisfying all active filters. Omitting all filters returns every track.
"""

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

_SUPPORTED = {".csv", ".json", ".parquet"}


def _load_file(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".json":
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
        records = raw.get("tracks", raw) if isinstance(raw, dict) else raw
        return pd.DataFrame(records)
    if suffix == ".parquet":
        return pd.read_parquet(path, engine="pyarrow")
    raise ValueError(f"Unsupported file type: {suffix}")


def load_data(source: str) -> pd.DataFrame:
    """Load a single data file or every data file in a directory."""
    p = Path(source)

    if p.is_file():
        if p.suffix.lower() not in _SUPPORTED:
            sys.exit(f"Unsupported file type '{p.suffix}'. Use CSV, JSON, or Parquet.")
        return _load_file(p)

    if p.is_dir():
        frames = [
            _load_file(f)
            for ext in _SUPPORTED
            for f in sorted(p.glob(f"*{ext}"))
        ]
        if not frames:
            sys.exit(f"No CSV, JSON, or Parquet files found in: {p}")
        df = pd.concat(frames, ignore_index=True)
        df = df.drop_duplicates(subset="track_id", keep="first")
        return df

    sys.exit(f"Path not found: {source}")

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def normalise_str(value: str, field: str) -> str:
    """Strip whitespace; reject empty strings."""
    v = value.strip()
    if not v:
        sys.exit(f"--{field} value must not be empty.")
    return v


def normalise_duration(raw: str) -> str:
    """Normalise M:SS or MM:SS → canonical M:SS (e.g. "03:39" → "3:39"). Rejects invalid input."""
    raw = raw.strip()
    parts = raw.split(":")
    if len(parts) != 2:
        sys.exit(f"Invalid duration '{raw}'. Expected M:SS format, e.g. 3:39 or 1:20.")
    try:
        minutes = int(parts[0])
        seconds = int(parts[1])
    except ValueError:
        sys.exit(f"Invalid duration '{raw}'. Minutes and seconds must be integers.")
    if not (0 <= seconds <= 59):
        sys.exit(f"Invalid duration '{raw}'. Seconds must be 0–59.")
    if minutes < 0:
        sys.exit(f"Invalid duration '{raw}'. Minutes must be non-negative.")
    return f"{minutes}:{seconds:02d}"


def normalise_name_list(values: list[str], field: str) -> list[str]:
    """Strip whitespace from each entry; exit on any blank entry."""
    cleaned = [v.strip() for v in values]
    blanks  = [v for v in cleaned if not v]
    if blanks:
        sys.exit(f"--{field} values must not be empty strings.")
    return cleaned

# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------

def _filter_exact(df: pd.DataFrame, col: str, value: str, ci: bool = False) -> pd.DataFrame:
    """Keep rows whose column value equals value (case-insensitive when ci=True)."""
    if ci:
        return df[df[col].astype(str).str.lower() == value.lower()]
    return df[df[col].astype(str) == value]


def _filter_list_all(df: pd.DataFrame, col: str, required: list[str], ci: bool = False) -> pd.DataFrame:
    """Keep rows where ALL entries in `required` appear in the comma-separated `col` (ci=True → case-insensitive)."""
    req = [r.lower() for r in required] if ci else required

    def row_matches(cell) -> bool:
        if pd.isna(cell):
            return False
        raw_values = {v.strip() for v in str(cell).split(",")}
        cell_values = {v.lower() for v in raw_values} if ci else raw_values
        return all(r in cell_values for r in req)

    return df[df[col].apply(row_matches)]


def apply_filters(df: pd.DataFrame, filters: dict) -> pd.DataFrame:
    """Apply active filters in AND-logic sequence. Missing/None values are skipped."""
    result = df

    # Case-sensitive exact-match filters (IDs, codes, timestamps)
    for col in ("track_id", "isrc", "album_id", "album_release_date", "added_at", "added_by", "duration"):
        val = filters.get(col)
        if val is not None:
            result = _filter_exact(result, col, val)

    # Case-insensitive exact-match filters (human-readable names)
    for col in ("track_name", "album_name"):
        val = filters.get(col)
        if val is not None:
            result = _filter_exact(result, col, val, ci=True)

    # Multi-value "all must match" filters
    # artists: case-insensitive; artist_ids: case-sensitive (opaque Spotify IDs)
    for key, col, ci in [("artists", "artists", True), ("artist_ids", "artist_ids", False)]:
        val = filters.get(key)
        if val:
            result = _filter_list_all(result, col, val, ci=ci)

    return result.reset_index(drop=True)

# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

ALPHA_SORT_COLS = {"track_name", "artists", "album_name", "added_by"}
DATE_SORT_COLS  = {"album_release_date", "added_at"}
SORT_COLUMNS    = ALPHA_SORT_COLS | {"duration"} | DATE_SORT_COLS


def _expand_release_date(val: str) -> str:
    """Pad partial Spotify release dates to YYYY-MM-DD for reliable pd.to_datetime parsing.
    "2011" → "2011-01-01",  "2011-05" → "2011-05-01",  "2011-05-01" → unchanged.
    """
    v = str(val).strip()
    if re.fullmatch(r"\d{4}", v):
        return v + "-01-01"
    if re.fullmatch(r"\d{4}-\d{2}", v):
        return v + "-01"
    return v


def sort_results(df: pd.DataFrame, sort_by: str | None, sort_order: str = "asc") -> pd.DataFrame:
    """Return df sorted by sort_by column; sort_by=None returns df unchanged.
    Alpha cols (track_name/artists/album_name/added_by): case-insensitive stable sort.
    duration: M:SS → seconds. Dates: chronological (added_at UTC-aware;
    album_release_date uses _expand_release_date for partial-date normalisation).
    """
    if sort_by is None:
        return df

    ascending = (sort_order == "asc")

    if sort_by in ALPHA_SORT_COLS:
        return (
            df.assign(_sort_key=df[sort_by].astype(str).str.lower())
              .sort_values("_sort_key", ascending=ascending, kind="stable")
              .drop(columns=["_sort_key"])
              .reset_index(drop=True)
        )

    if sort_by == "duration":
        def _dur_secs(val: str) -> int:
            try:
                m, s = str(val).split(":")
                return int(m) * 60 + int(s)
            except Exception:
                return -1

        return (
            df.assign(_sort_key=df["duration"].apply(_dur_secs))
              .sort_values("_sort_key", ascending=ascending, kind="stable")
              .drop(columns=["_sort_key"])
              .reset_index(drop=True)
        )

    if sort_by in DATE_SORT_COLS:
        if sort_by == "album_release_date":
            col = df[sort_by].apply(_expand_release_date)
            parsed = pd.to_datetime(col, errors="coerce")
        else:  # added_at — timezone-aware ISO strings
            parsed = pd.to_datetime(df[sort_by], errors="coerce", utc=True)
        return (
            df.assign(_sort_key=parsed)
              .sort_values("_sort_key", ascending=ascending, na_position="last", kind="stable")
              .drop(columns=["_sort_key"])
              .reset_index(drop=True)
        )

    sys.exit(
        f"Invalid --sort-by column '{sort_by}'. "
        f"Valid options: {', '.join(sorted(SORT_COLUMNS))}"
    )

# ---------------------------------------------------------------------------
# Column selection
# ---------------------------------------------------------------------------

def resolve_columns(df: pd.DataFrame, requested: list[str] | None) -> list[str]:
    """Validate and return the columns to display; all columns if none requested."""
    if not requested:
        return list(df.columns)
    available = set(df.columns)
    invalid = [c for c in requested if c not in available]
    if invalid:
        sys.exit(
            f"Unknown column(s): {', '.join(invalid)}\n"
            "Run with --list-columns to see available columns."
        )
    return requested

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def list_columns(df: pd.DataFrame) -> None:
    print("Available columns:")
    for col in df.columns:
        print(f"  {col}")


def _filter_summary(filters: dict) -> str:
    """Build a compact human-readable summary of all active filters."""
    parts = []
    for key, val in filters.items():
        if val is None:
            continue
        if isinstance(val, list):
            parts.append(f"{key}=[{', '.join(val)}]")
        else:
            parts.append(f"{key}='{val}'")
    return ", ".join(parts)


def display_results(df: pd.DataFrame, filters: dict, columns: list[str]) -> None:
    summary = _filter_summary(filters)
    if df.empty:
        if summary:
            print(f"No tracks found matching: {summary}")
        else:
            print("No tracks found.")
        return

    count = len(df)
    label = "track" if count == 1 else "tracks"
    if summary:
        print(f"\n{count} {label} matching {summary}:\n")
    else:
        print(f"\n{count} {label}:\n")
    with pd.option_context(
        "display.max_columns", None,
        "display.max_rows",    None,
        "display.width",       None,
        "display.max_colwidth", None,
    ):
        print(df[columns].to_string(index=True))


def save_results(df: pd.DataFrame, columns: list[str], output_dir: str) -> str:
    """Save df[columns] to a timestamped CSV in output_dir. Returns the saved path."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts   = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = out / f"analysis_{ts}.csv"
    df[columns].to_csv(path, index=False)
    print(f"Saved to {path}")
    return str(path)

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="analyser",
        description=(
            "Analyse and filter Spotify playlist data produced by spotify_fetcher.\n"
            "Loads one or more fetcher output files and returns every track that\n"
            "satisfies ALL provided filters simultaneously (AND logic).\n"
            "Filters are optional — omitting all filter flags returns every track."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python analyser/analyser.py --file spotify_data/ --list-columns
  python analyser/analyser.py --file spotify_data/ --duration 3:39
  python analyser/analyser.py --file spotify_data/ --artists "Diljit Dosanjh"
  python analyser/analyser.py --file spotify_data/ --artists "Harrdy Sandhu" "Jaani"
  python analyser/analyser.py --file spotify_data/ --artist-ids 2RJawMqX9ESxws2KMtHyP3
  python analyser/analyser.py --file spotify_data/ --track-name "Kangna"
  python analyser/analyser.py --file spotify_data/ --album-name "Do Gabru - Diljit Dosanjh & Akhil" --duration 3:18
  python analyser/analyser.py --file spotify_data/ --added-by <spotify_user_id> --columns track_name artists duration
  python analyser/analyser.py --file spotify_data/ --artists "Yo Yo Honey Singh" --sort-by track_name
  python analyser/analyser.py --file spotify_data/ --artists "Yo Yo Honey Singh" --sort-by duration --sort-order desc
""",
    )

    p.add_argument(
        "--file", required=True,
        metavar="PATH",
        help=(
            "Path to a fetcher output file or a directory of such files. "
            "Accepted formats: CSV, JSON, Parquet. "
            "When a directory is given, all matching files are loaded and "
            "de-duplicated on track_id before filtering."
        ),
    )

    # --- Filter arguments ---
    p.add_argument(
        "--track-id",
        metavar="ID",
        help=(
            "Filter by exact Spotify track ID (e.g. 7ejqSzofWfLiqBT9CcCmsf). "
            "Must match the track_id column exactly."
        ),
    )
    p.add_argument(
        "--track-name",
        metavar="NAME",
        help=(
            "Filter by track name (e.g. 'Kangna'). "
            "Matching is case-insensitive — 'kangna', 'Kangna', and 'KANGNA' all match the same rows."
        ),
    )
    p.add_argument(
        "--isrc",
        metavar="ISRC",
        help=(
            "Filter by exact ISRC code (e.g. INV111700211). "
            "Must match the isrc column exactly."
        ),
    )
    p.add_argument(
        "--artists", nargs="+",
        metavar="ARTIST",
        help=(
            "Filter by artist name(s), space-separated. "
            "A single name returns tracks where that artist appears anywhere in the artists field. "
            "Multiple names return only tracks that include ALL of those artists "
            "(e.g. --artists 'Harrdy Sandhu' 'Jaani' returns tracks featuring both). "
            "Matching is case-insensitive — 'diljit', 'Diljit', and 'DILJIT' all match the same rows."
        ),
    )
    p.add_argument(
        "--artist-ids", nargs="+",
        metavar="ID",
        help=(
            "Filter by Spotify artist ID(s), space-separated. "
            "Follows the same single/multi-value logic as --artists: "
            "one ID returns any track featuring that artist; "
            "multiple IDs return only tracks featuring ALL of them. "
            "Must match the artist_ids column exactly."
        ),
    )
    p.add_argument(
        "--album-name",
        metavar="NAME",
        help=(
            "Filter by album name. "
            "Matching is case-insensitive — 'do gabru', 'Do Gabru', and 'DO GABRU' all match the same rows."
        ),
    )
    p.add_argument(
        "--album-id",
        metavar="ID",
        help=(
            "Filter by exact Spotify album ID (e.g. 1uxDllRe9CPhdr8rhz2QCZ). "
            "Must match the album_id column exactly."
        ),
    )
    p.add_argument(
        "--album-release-date",
        metavar="DATE",
        help=(
            "Filter by exact album release date as stored in the data "
            "(e.g. 2019-07-08, 2017, 2012-06). "
            "Must match the album_release_date column exactly."
        ),
    )
    p.add_argument(
        "--added-at",
        metavar="DATETIME",
        help=(
            "Filter by the exact datetime the track was added to the playlist "
            "(e.g. '2019-08-04 20:18:18+00:00'). "
            "Must match the added_at column exactly as stored."
        ),
    )
    p.add_argument(
        "--added-by",
        metavar="USER_ID",
        help=(
            "Filter by the Spotify user ID of whoever added the track to the playlist. "
            "Must match the added_by column exactly."
        ),
    )
    p.add_argument(
        "--duration",
        metavar="M:SS",
        help=(
            "Filter by exact track duration in M:SS format (e.g. 3:39, 1:20, 0:00). "
            "Leading zeros on minutes are accepted and normalised (03:39 → 3:39). "
            "Matched against the duration column written by the fetcher."
        ),
    )

    # --- Output arguments ---
    p.add_argument(
        "--columns", nargs="+",
        metavar="COL",
        help=(
            "One or more column names to include in the output, space-separated "
            "(e.g. --columns track_name artists duration popularity). "
            "Column names must match exactly — use --list-columns to see valid names. "
            "Omit this flag to display all columns."
        ),
    )
    p.add_argument(
        "--list-columns", action="store_true",
        help=(
            "Print all column names available in the loaded data file(s) and exit. "
            "No filter flag is required when this option is used."
        ),
    )
    p.add_argument(
        "--sort-by",
        metavar="COLUMN",
        choices=sorted(SORT_COLUMNS),
        help=(
            "Sort the output by this column. "
            "Alphabetical sort (case-insensitive): track_name, artists, album_name, added_by. "
            "Duration sort (shortest → longest): duration. "
            "Chronological sort: album_release_date, added_at. "
            "Use --sort-order to control direction (default: asc). "
            f"Valid choices: {', '.join(sorted(SORT_COLUMNS))}."
        ),
    )
    p.add_argument(
        "--sort-order",
        choices=["asc", "desc"],
        default="asc",
        help=(
            "Sort direction when --sort-by is used. "
            "'asc' = ascending (A→Z, shortest first, oldest first). "
            "'desc' = descending (Z→A, longest first, newest first). "
            "Default: asc. Has no effect without --sort-by."
        ),
    )
    p.add_argument(
        "--save", action="store_true",
        help=(
            "Save the filtered results to a CSV file in analysed_data/ at the project root. "
            "The file is named analysis_<YYYYMMDD_HHMMSS>.csv and contains the same columns "
            "as the screen output. The directory is created automatically if it does not exist."
        ),
    )

    return p


def main() -> None:
    args = build_parser().parse_args()
    df   = load_data(args.file)

    if args.list_columns:
        list_columns(df)
        return

    filters: dict = {
        "track_id":           normalise_str(args.track_id, "track-id") if args.track_id else None,
        "track_name":         normalise_str(args.track_name, "track-name") if args.track_name else None,
        "isrc":               normalise_str(args.isrc, "isrc") if args.isrc else None,
        "artists":            normalise_name_list(args.artists, "artists") if args.artists else None,
        "artist_ids":         normalise_name_list(args.artist_ids, "artist-ids") if args.artist_ids else None,
        "album_name":         normalise_str(args.album_name, "album-name") if args.album_name else None,
        "album_id":           normalise_str(args.album_id, "album-id") if args.album_id else None,
        "album_release_date": normalise_str(args.album_release_date, "album-release-date") if args.album_release_date else None,
        "added_at":           normalise_str(args.added_at, "added-at") if args.added_at else None,
        "added_by":           normalise_str(args.added_by, "added-by") if args.added_by else None,
        "duration":           normalise_duration(args.duration) if args.duration else None,
    }

    columns = resolve_columns(df, args.columns)
    result  = apply_filters(df, filters)
    result  = sort_results(result, args.sort_by, args.sort_order)
    display_results(result, filters, columns)
    if args.save:
        save_results(result, columns, str(Path(__file__).parent.parent / "analysed_data"))


if __name__ == "__main__":
    main()
