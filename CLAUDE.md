# CLAUDE.md

## Environment

Python 3.14.3 via pyenv. Always activate the venv:

```bash
source .venv/bin/activate
```

Details in `.venv_info`. Never install outside the venv.

## Credentials

Copy `.env.example` to `.env`; set `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`. Redirect URI: `http://127.0.0.1:8888/callback` (IP not `localhost` — Spotify requires this).

## Project layout

```
SpotifyAppWork/
├── fetcher/        # spotify_fetcher.py — pulls data from Spotify
├── analyser/       # analyser.py — filters/sorts fetcher output
├── tests/          # pytest suite (test_analyser.py, test_fetcher.py, conftest.py)
├── spotify_data/   # fetched output files (CSV / JSON / Parquet) — read-only for tests
├── analysed_data/  # analyser --save output (gitignored)
├── pytest.ini      # pythonpath = analyser fetcher
└── .venv/
```

The fetcher always writes to `spotify_data/` regardless of working directory.

## Running the fetcher

```bash
python fetcher/spotify_fetcher.py --playlist <id_or_url>
python fetcher/spotify_fetcher.py --playlist <id1> <id2> <id3>
python fetcher/spotify_fetcher.py --playlist <id_or_url> --market GB --format csv json parquet
```

`--playlist` accepts one or more space-separated values; each can be a bare ID, `spotify:playlist:` URI, or full `open.spotify.com` URL. Multiple playlists are each saved to their own separate file. Authentication happens once and is shared across all playlists.

## Running the analyser

```bash
python analyser/analyser.py --file spotify_data/ --list-columns
python analyser/analyser.py --file spotify_data/ --columns track_id track_name artists album_name album_release_date duration  # all tracks
python analyser/analyser.py --file spotify_data/ --columns track_name duration --sort-by duration --sort-order desc
python analyser/analyser.py --file spotify_data/ --artists "Diljit Dosanjh"
python analyser/analyser.py --file spotify_data/ --artists "Harrdy Sandhu" "Jaani"  # must feature BOTH
python analyser/analyser.py --file spotify_data/ --track-name "Kangna" --columns track_name artists duration
python analyser/analyser.py --file spotify_data/ --album-name "Do Gabru" --duration 3:18
python analyser/analyser.py --file spotify_data/ --artists "Yo Yo Honey Singh" --sort-by track_name
python analyser/analyser.py --file spotify_data/ --artists "Yo Yo Honey Singh" --sort-by duration --sort-order desc
python analyser/analyser.py --file spotify_data/ --duration 0:00 --save
```

**Filter flags (all optional):** `--track-id`, `--track-name`, `--isrc`, `--artists`, `--artist-ids`, `--album-name`, `--album-id`, `--album-release-date`, `--added-at`, `--added-by`, `--duration`

**Filter behaviour:**
- Case-insensitive: `track_name`, `album_name`, `artists`. All others: exact, case-sensitive.
- Multiple `--artists`/`--artist-ids` values require ALL to appear.
- `--duration` normalises leading zeros (`03:39` → `3:39`). All filters AND; omitting all returns every track.
- `--columns` selects output columns (omit for all); `--list-columns` needs no filter.

**Sorting flags (optional):**
- `--sort-by COL` — `track_name`, `artists`, `album_name`, `added_by` (alpha, case-insensitive); `duration` (M:SS → seconds); `album_release_date`, `added_at` (chronological).
- `--sort-order {asc|desc}` — default `asc`.

**Output flag (optional):**
- `--save` — saves the filtered results to `analysed_data/analysis_<YYYYMMDD_HHMMSS>.csv` at the project root. Directory is created automatically. File contains the same columns as the screen output.

## Architecture

### Fetcher (`fetcher/spotify_fetcher.py`) — stable, treat as read-only unless explicitly asked to change it

1. **Auth** (`build_spotify_client`) — `SpotifyOAuth` with a file-based token cache (`.spotify_cache`); browser opens on first run only.
2. **Playlist metadata** (`fetch_playlist_info`) — single `sp.playlist()` call.
3. **Track pagination** (`fetch_all_tracks`) — `sp.playlist_items()` in pages of 100. `_extract_track` reads `item.get("item") or item.get("track")` (`"track"` key is deprecated).
4. **Extraction** (`_extract_track`) — flattens a playlist item into a row; skips podcast episodes and null items.
5. **DataFrame** — nullable `Int64` dtypes for numeric columns, UTC-aware timestamps for `added_at`.
6. **Persistence** (`save_dataframe`) — writes CSV, JSON, and/or Parquet per `--format`.

### Analyser (`analyser/analyser.py`) — complete, treat as read-only unless explicitly asked to change it

1. **I/O** (`load_data`) — single file or directory; directory mode concatenates and de-dupes on `track_id`. JSON expects `"tracks"` key or bare list.
2. **Normalisation** — `normalise_str`/`normalise_name_list` (strip whitespace, reject blanks; case preserved), `normalise_duration` (M:SS).
3. **Filtering** (`apply_filters`) — `_filter_exact` (case-insensitive for `track_name`/`album_name`, sensitive otherwise); `_filter_list_all` (comma-split; case-insensitive for `artists`, sensitive for `artist_ids`).
4. **Sorting** (`sort_results`) — stable sort; `SORT_COLUMNS` lists valid columns. Alpha: lowercased key. Duration: M:SS → seconds. `added_at`: `pd.to_datetime(utc=True)`. `album_release_date`: `_expand_release_date` pads `YYYY`→`YYYY-01-01` and `YYYY-MM`→`YYYY-MM-01` before parsing — prevents NaT for minority formats in a mixed column; `"0000"` sorts first.
5. **Column selection** (`resolve_columns`) — validates; rejects unknowns.
6. **Display** (`display_results`) — `df.to_string()` in `pd.option_context`; no active filters → `"N tracks:"` header (omits "matching …").
7. **Save** (`save_results`) — writes `df[columns]` to `analysed_data/analysis_<timestamp>.csv`; called from `main()` when `--save` is set.

## Testing

```bash
pytest                        # run all tests
pytest tests/test_analyser.py
pytest tests/test_fetcher.py
```

- `conftest.py` — 5-track `SAMPLE_TRACKS`, session-scoped CSV/JSON/Parquet fixtures, `real_csv` (auto-discovers the first CSV in `spotify_data/` alphabetically; skips if none present — never hardcoded to a specific filename).
- Tests must never write to `spotify_data/`. Generated data goes in `tmp_path`/`tmp_path_factory`.

## Development methodology

TDD: **Red** → **Green** → **Refactor**; never skip to implementation. Test class order mirrors source function order.

## Git workflow

Branch-work-merge: every new feature gets its own branch.

```bash
git checkout -b <feature-name>   # before writing any code
# ... commits on the branch ...
git checkout main
git merge <feature-name>         # only when feature is fully complete
```

"Fully complete" means: feature implemented, tests written and passing, everything done. Never commit new features directly to `main`.

## Script conventions

Every script: `--help` via `argparse RawDescriptionHelpFormatter`; each arg's `help=` covers accepted values, default, and normalisation; `epilog` with concrete project-root examples.

## Spotify fields filter rules

Always use parenthetical notation — never mix dot notation inside a parenthetical block (Spotify silently drops such fields):

```
# WRONG:   items(added_by.id, track(...))
# CORRECT: items(added_by(id), item(...))
```
