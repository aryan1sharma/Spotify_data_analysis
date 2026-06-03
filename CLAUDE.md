# CLAUDE.md

## Environment

Python 3.14.3 via pyenv. Activate the venv:

```bash
source .venv/bin/activate
```

Details in `.venv_info`. Never install outside the venv.

**Tcl/Tk (GUI only):** must exist *before* pyenv builds Python or `_tkinter` is silently omitted. Fix `ModuleNotFoundError: No module named '_tkinter'`:
```bash
brew install tcl-tk
LDFLAGS="-L$(brew --prefix tcl-tk)/lib" CPPFLAGS="-I$(brew --prefix tcl-tk)/include" \
PKG_CONFIG_PATH="$(brew --prefix tcl-tk)/lib/pkgconfig" pyenv install --force 3.14.3
python -m venv .venv --clear && source .venv/bin/activate && pip install -r requirements.txt
```

## Credentials

Copy `.env.example` to `.env`; set `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Redirect URI: `http://127.0.0.1:8888/callback` (IP not `localhost` — Spotify requires this).

## Project layout

```
SpotifyDataAnalysis/
├── fetcher/        # spotify_fetcher.py — pulls data from Spotify
├── analyser/       # analyser.py — filters/sorts fetcher output
├── gui/            # app.py (Tkinter shell) + logic.py (pure functions, tested)
├── tests/          # pytest suite (test_analyser.py, test_fetcher.py, test_gui.py, conftest.py)
├── spotify_data/   # fetched output files (CSV / JSON / Parquet) — read-only for tests
├── analysed_data/  # analyser --save output (gitignored)
├── pytest.ini      # pythonpath = analyser fetcher gui
└── .venv/
```

Fetcher always writes to `spotify_data/` regardless of working directory.

## Running the fetcher

```bash
python fetcher/spotify_fetcher.py --playlist <id_or_url>
python fetcher/spotify_fetcher.py --playlist <id1> <id2> <id3>
python fetcher/spotify_fetcher.py --playlist <id_or_url> --market GB --format csv json parquet
```

`--playlist` accepts bare IDs, `spotify:playlist:` URIs, or `open.spotify.com` URLs (space-separated). Multiple playlists each save to their own file; auth is shared.

## Running the analyser

```bash
python analyser/analyser.py --file spotify_data/ --list-columns
python analyser/analyser.py --file spotify_data/ --columns track_name duration --sort-by duration --sort-order desc
python analyser/analyser.py --file spotify_data/ --artists "Harrdy Sandhu" "Jaani"  # must feature BOTH
python analyser/analyser.py --file spotify_data/ --track-name "Kangna" --columns track_name artists duration
python analyser/analyser.py --file spotify_data/ --album-name "Do Gabru" --duration 3:18
python analyser/analyser.py --file spotify_data/ --artists "Yo Yo Honey Singh" --sort-by duration --sort-order desc
python analyser/analyser.py --file spotify_data/ --duration 0:00 --save
```

**Filter flags:** `--track-id`, `--track-name`, `--isrc`, `--artists`, `--artist-ids`, `--album-name`, `--album-id`, `--album-release-date`, `--added-at`, `--added-by`, `--duration`

**Filter behaviour:**
- Case-insensitive: `track_name`, `album_name`, `artists`. All others: exact, case-sensitive.
- Multiple `--artists`/`--artist-ids` values require ALL to appear.
- `--duration` normalises leading zeros (`03:39` → `3:39`). All filters AND; omitting all returns every track.
- `--columns` selects output columns (omit for all); `--list-columns` needs no filter.

**Sort:** `--sort-by` accepts `track_name`, `artists`, `album_name`, `added_by` (alpha); `duration` (M:SS → s); `album_release_date`, `added_at` (chronological). `--sort-order {asc|desc}` default `asc`.

**Save:** `--save` writes `analysed_data/analysis_<YYYYMMDD_HHMMSS>.csv` (same columns as screen output; directory auto-created).

## Architecture

### Fetcher (`fetcher/spotify_fetcher.py`) — read-only unless asked

1. **Auth** (`build_spotify_client`) — `SpotifyOAuth`, file-based token cache (`.spotify_cache`); browser opens once.
2. **Metadata** (`fetch_playlist_info`) — single `sp.playlist()` call.
3. **Pagination** (`fetch_all_tracks`) — `sp.playlist_items()` pages of 100; reads `item.get("item") or item.get("track")` (`"track"` deprecated).
4. **Extraction** (`_extract_track`) — flattens to row; skips podcasts and nulls.
5. **DataFrame** — nullable `Int64`, UTC-aware `added_at`.
6. **Persistence** (`save_dataframe`) — CSV/JSON/Parquet per `--format`.

### Analyser (`analyser/analyser.py`) — read-only unless asked

1. **I/O** (`load_data`) — single file or directory (concat + dedup on `track_id`). JSON: `"tracks"` key or bare list.
2. **Normalisation** — `normalise_str`/`normalise_name_list` (strip, reject blanks), `normalise_duration` (M:SS).
3. **Filtering** (`apply_filters`) — `_filter_exact` (case-insensitive for `track_name`/`album_name`); `_filter_list_all` (comma-split; case-insensitive for `artists`).
4. **Sorting** (`sort_results`) — stable; `SORT_COLUMNS` valid set. `album_release_date`: `_expand_release_date` pads `YYYY`→`YYYY-01-01`, `YYYY-MM`→`YYYY-MM-01`; `"0000"` sorts first.
5. **Column selection** (`resolve_columns`) — validates, rejects unknowns.
6. **Display** (`display_results`) — `df.to_string()`; no active filters → `"N tracks:"` header.
7. **Save** (`save_results`) — `analysed_data/analysis_<timestamp>.csv` from `main()` when `--save` set.

## Running the GUI

```bash
python gui/app.py
```

Two tabs:

**Fetcher** — playlist IDs/URLs (one per line), market/format options, Fetch button. Live output streams to console pane (no wrap, h-scroll). Progress bar fills per playlist; status: `Running — Fetching X/Y` → `Done ✓`. Fetch disabled while running.

**Analyser** — select one or more CSVs from `spotify_data/` (Ctrl/Shift+click; dedup on `track_id`), click Analyse once. All controls are then **live** (instant, no re-click):
- **Filters** (collapsible ▼/▶, starts collapsed): substring match for `track_name`, `artists`, `album_name`, `album_release_date`, `added_at`; exact for IDs/ISRC/duration/added_by. Invalid/partial values silently ignored.
- **Sort** (always visible): Sort by / Order dropdowns, instant.
- **Columns**: checkboxes (all unchecked = show all). Double-click column separator → auto-fit width.
- **Save**: tick before Analyse to write `analysed_data/`.

### GUI architecture (`gui/`)

| File | Purpose |
|------|---------|
| `gui/logic.py` | Pure functions — no Tkinter, fully unit-tested |
| `gui/app.py`   | Tkinter shell — `App`, `FetcherTab`, `AnalyserTab`, `ScrollableTable`, `CollapsibleSection`; not unit-tested |

**`logic.py` public functions:**
- `discover_csvs(data_dir)` — sorted CSVs in directory
- `build_fetcher_command(playlist_ids, market, formats)` — subprocess cmd; `None` if no valid IDs; always `-u` (unbuffered stdout for real-time streaming)
- `build_filter_dict(form_state)` — blank → `None`; `artists`/`artist_ids` → comma-split lists
- `dataframe_to_treeview_rows(df)` — NaN → `""`; all `str`

**`ScrollableTable`** (`ttk.Treeview`): `stretch=False`; V-scroll: wheel; H-scroll: `Shift+Wheel` or scrollbar (2% fractional steps). Double-click column separator → `_autofit_column` (font measure + 20 px).

**`CollapsibleSection`** — Filters only; `▶`/`▼` toggle; starts collapsed.

**`AnalyserTab` live-update pipeline:**
```
_on_analyse      → load + dedup → _loaded_df
_refresh_filters → str.contains (track_name/artists/album_name/album_release_date/added_at)
                   + ana.apply_filters (IDs/ISRC/duration/added_by)
                 → _filtered_df → _refresh_display()
_refresh_display → sort + col selection → table + label
                 → caches _display_result / _display_columns (used by Save)
```
- Filter `StringVar`s: `trace_add("write")` → `_refresh_filters()`
- `_SUBSTRING_FILTER_FIELDS` frozenset controls which fields use contains vs exact
- Sort `StringVar`s + column `BooleanVar`s: `trace_add("write")` → `_refresh_display()`
- `_on_analyse`: explicit button only (reloads files, re-filters, re-displays)
- Save: uses cached `_display_result` / `_display_columns`

**Threading:** Fetcher subprocess in `daemon=True` thread → `queue.Queue` → `root.after(100, ...)` polls on main thread. Widget state never mutated from worker.

## Testing

```bash
pytest                         # all tests
pytest tests/test_analyser.py
pytest tests/test_fetcher.py
pytest tests/test_gui.py       # tests logic.py only; app.py is not unit-tested
```

`conftest.py`: 5-track `SAMPLE_TRACKS`, session-scoped CSV/JSON/Parquet fixtures, `real_csv` (first CSV in `spotify_data/` alphabetically; skips if absent). Tests must never write to `spotify_data/`; use `tmp_path`/`tmp_path_factory`.

## Development methodology

TDD: **Red → Green → Refactor**; never skip to implementation. Test class order mirrors source function order.

## Git workflow

Every feature on its own branch; merge to `main` only when fully complete (implemented + tests passing).

```bash
git checkout -b <feature-name>
# work...
git checkout main && git merge <feature-name>
```

Never commit features directly to `main`.

## Script conventions

Every script: `--help` via `argparse RawDescriptionHelpFormatter`; each arg's `help=` covers accepted values, default, and normalisation; `epilog` with concrete project-root examples.

## Spotify fields filter rules

Always use parenthetical notation — never dot notation inside parenthetical blocks (Spotify silently drops them):

```
# WRONG:   items(added_by.id, track(...))
# CORRECT: items(added_by(id), item(...))
```
