#!/usr/bin/env python3
"""Fetch all tracks from a Spotify playlist and save them locally as CSV/JSON/Parquet."""

import os
import sys
import json
import argparse
import datetime
from pathlib import Path

import pandas as pd
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.cache_handler import CacheFileHandler
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCOPES = " ".join([
    "playlist-read-private",
    "playlist-read-collaborative",
    "user-read-private",
    "user-read-email",
    "user-library-read",
])

# Fields to request per track item — minimizes payload / rate-limit cost
_TRACK_FIELDS = (
    "items("
    "added_at,"
    "added_by(id),"
    "item("
    "id,name,type,duration_ms,explicit,popularity,"
    "track_number,disc_number,is_local,preview_url,"
    "external_ids,external_urls,"
    "artists(id,name),"
    "album(id,name,album_type,release_date,images)"
    ")"
    "),total,next"
)

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def build_spotify_client(cache_path: str = ".spotify_cache") -> spotipy.Spotify:
    """Return an authenticated Spotify client. First run opens browser for consent;
    token (including refresh) is written to cache_path for reuse on subsequent runs."""
    client_id = os.getenv("SPOTIFY_CLIENT_ID")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET")
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")

    if not client_id or not client_secret:
        sys.exit(
            "ERROR: SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET must be set.\n"
            "Copy .env.example to .env and fill in your credentials."
        )

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=CacheFileHandler(cache_path=cache_path),
        open_browser=True,
        show_dialog=False,  # only show consent screen when no valid token exists
    )

    return spotipy.Spotify(auth_manager=auth_manager)

# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def _ms_to_mmss(ms: int) -> str:
    minutes, seconds = divmod(ms // 1000, 60)
    return f"{minutes}:{seconds:02d}"


def _extract_track(item: dict, market: str) -> dict | None:
    """
    Flatten a single playlist-item dict into a row-ready dict.
    Returns None for podcast episodes, local files without IDs, or null tracks.
    """
    track = item.get("item") or item.get("track")
    if not track:
        return None
    if track.get("type") != "track":
        return None  # skip podcast episodes

    artists = track.get("artists", [])
    album   = track.get("album", {})
    images  = album.get("images", [])
    duration_ms: int = track.get("duration_ms", 0)

    return {
        # Track identity
        "track_id":           track.get("id"),
        "track_name":         track.get("name"),
        "isrc":               track.get("external_ids", {}).get("isrc"),
        "spotify_url":        track.get("external_urls", {}).get("spotify"),
        "preview_url":        track.get("preview_url"),
        "is_local":           track.get("is_local", False),
        # Artists
        "artists":            ", ".join(a["name"] for a in artists),
        "artist_ids":         ", ".join(a["id"] for a in artists if a.get("id")),
        # Album
        "album_name":         album.get("name"),
        "album_id":           album.get("id"),
        "album_type":         album.get("album_type"),
        "album_release_date": album.get("release_date"),
        "album_cover_url":    images[0]["url"] if images else None,
        # Playback
        "duration_ms":        duration_ms,
        "duration":           _ms_to_mmss(duration_ms),
        "explicit":           track.get("explicit", False),
        "popularity":         track.get("popularity"),
        "track_number":       track.get("track_number"),
        "disc_number":        track.get("disc_number"),
        # Playlist context
        "market":             market,
        "added_at":           item.get("added_at"),
        "added_by":           item.get("added_by", {}).get("id"),
    }

# ---------------------------------------------------------------------------
# Spotify API calls
# ---------------------------------------------------------------------------

def fetch_playlist_info(sp: spotipy.Spotify, playlist_id: str, market: str) -> dict:
    pl = sp.playlist(
        playlist_id,
        market=market,
        fields="id,name,description,public,collaborative,owner,followers,images,tracks(total)",
    )
    images = pl.get("images") or []
    return {
        "playlist_id":   pl.get("id"),
        "playlist_name": pl.get("name"),
        "description":   pl.get("description"),
        "owner":         pl.get("owner", {}).get("display_name"),
        "owner_id":      pl.get("owner", {}).get("id"),
        "public":        pl.get("public"),
        "collaborative": pl.get("collaborative"),
        "followers":     pl.get("followers", {}).get("total"),
        "total_tracks":  pl.get("tracks", {}).get("total"),
        "cover_image":   images[0]["url"] if images else None,
    }


def fetch_all_tracks(sp: spotipy.Spotify, playlist_id: str, market: str) -> list[dict]:
    """Page through all tracks and return a list of flattened row dicts."""
    rows: list[dict] = []
    offset = 0
    limit  = 100  # Spotify maximum

    print(f"Fetching tracks (market={market})...")
    while True:
        result = sp.playlist_items(
            playlist_id,
            market=market,
            limit=limit,
            offset=offset,
            fields=_TRACK_FIELDS,
        )
        items = result.get("items", [])
        total = result.get("total", 0)

        for item in items:
            row = _extract_track(item, market)
            if row:
                rows.append(row)

        fetched = min(offset + limit, total)
        print(f"  {fetched}/{total} items processed...")

        if not result.get("next"):
            break
        offset += limit

    return rows

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def save_dataframe(
    df: pd.DataFrame,
    playlist_info: dict,
    output_dir: str,
    formats: list[str],
) -> list[str]:
    """Write *df* to one or more file formats; return list of saved paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    safe_name = "".join(
        c if c.isalnum() or c in " _-" else "_"
        for c in (playlist_info["playlist_name"] or "playlist")
    ).strip().replace(" ", "_")

    ts        = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{safe_name}_{ts}"
    saved: list[str] = []

    if "csv" in formats:
        path = out / f"{base_name}.csv"
        df.to_csv(path, index=False, encoding="utf-8")
        print(f"  CSV     → {path}")
        saved.append(str(path))

    if "json" in formats:
        path = out / f"{base_name}.json"
        payload = {
            "playlist":   playlist_info,
            "fetched_at": datetime.datetime.now(datetime.UTC).isoformat() + "Z",
            "tracks":     df.to_dict(orient="records"),
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
        print(f"  JSON    → {path}")
        saved.append(str(path))

    if "parquet" in formats:
        path = out / f"{base_name}.parquet"
        df.to_parquet(path, index=False, engine="pyarrow")
        print(f"  Parquet → {path}")
        saved.append(str(path))

    return saved

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_playlist_id(raw: str) -> str:
    """Accept a bare ID, a spotify: URI, or an open.spotify.com URL."""
    if "spotify.com/playlist/" in raw:
        return raw.split("playlist/")[1].split("?")[0]
    if "spotify:playlist:" in raw:
        return raw.split("spotify:playlist:")[1]
    return raw.strip()


def print_summary(df: pd.DataFrame) -> None:
    total_ms = int(df["duration_ms"].sum())
    total_min, rem_sec = divmod(total_ms // 1000, 60)
    total_hr,  rem_min = divmod(total_min, 60)

    print("\n--- Playlist Summary ---")
    print(f"  Tracks fetched : {len(df)}")
    print(f"  Unique artists : {df['artists'].nunique()}")
    print(f"  Unique albums  : {df['album_name'].nunique()}")
    print(f"  Total duration : {total_hr}h {rem_min}m {rem_sec}s")
    print(f"  Explicit tracks: {df['explicit'].sum()}")
    if "popularity" in df.columns:
        avg_pop = df["popularity"].dropna()
        if not avg_pop.empty:
            print(f"  Avg popularity : {avg_pop.mean():.1f}/100")

    print("\nFirst 10 tracks:")
    cols = ["track_name", "artists", "album_name", "duration", "popularity"]
    print(df[cols].head(10).to_string(index=False))

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="spotify_fetcher",
        description=(
            "Fetch all tracks from a Spotify playlist and save them locally.\n"
            "On first run a browser window opens for Spotify login. The OAuth\n"
            "token is cached so subsequent runs proceed without prompting."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python fetcher/spotify_fetcher.py --playlist 37i9dQZF1DXcBWIGoYBM5M
  python fetcher/spotify_fetcher.py --playlist https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
  python fetcher/spotify_fetcher.py --playlist 37i9dQZF1DXcBWIGoYBM5M --market GB
  python fetcher/spotify_fetcher.py --playlist 37i9dQZF1DXcBWIGoYBM5M --format csv json parquet
  python fetcher/spotify_fetcher.py --playlist 37i9dQZF1DXcBWIGoYBM5M --output-dir ./my_data
""",
    )
    p.add_argument(
        "--playlist", required=True,
        metavar="ID_OR_URL",
        help=(
            "Spotify playlist to fetch. Accepts a bare ID "
            "(e.g. 37i9dQZF1DXcBWIGoYBM5M), a spotify:playlist: URI, "
            "or a full open.spotify.com URL."
        ),
    )
    p.add_argument(
        "--market", default=None,
        metavar="CC",
        help=(
            "ISO 3166-1 alpha-2 country code used to filter track availability "
            "(e.g. US, GB, DE, IN). Tracks unavailable in the given market are "
            "excluded from results. Defaults to the country set on your Spotify account."
        ),
    )
    p.add_argument(
        "--format", nargs="+",
        choices=["csv", "json", "parquet"],
        default=["csv"],
        metavar="FMT",
        help=(
            "One or more output formats to write: csv, json, parquet. "
            "Multiple values are space-separated (e.g. --format csv json). "
            "Default: csv."
        ),
    )
    p.add_argument(
        "--output-dir", default=str(Path(__file__).parent.parent / "spotify_data"),
        metavar="DIR",
        help=(
            "Directory where output files are written. Created automatically if "
            "it does not exist. Files are named <playlist_name>_<timestamp>.<ext>. "
            "Default: <project_root>/spotify_data."
        ),
    )
    p.add_argument(
        "--cache-path", default=".spotify_cache",
        metavar="FILE",
        help=(
            "Path to the file used to store the Spotify OAuth token between runs. "
            "Delete this file to force re-authentication. Default: .spotify_cache."
        ),
    )
    return p


def main() -> None:
    args = build_parser().parse_args()

    playlist_id = parse_playlist_id(args.playlist)

    # --- Authenticate ---
    print("Authenticating with Spotify...")
    sp = build_spotify_client(cache_path=args.cache_path)

    user   = sp.current_user()
    market = args.market or user.get("country")

    print(f"Signed in : {user.get('display_name')} ({user.get('email')})")
    print(f"Market    : {market or 'none (all markets)'}")

    # --- Playlist metadata ---
    print("\nFetching playlist metadata...")
    playlist_info = fetch_playlist_info(sp, playlist_id, market)
    print(f"Playlist  : {playlist_info['playlist_name']}")
    print(f"Owner     : {playlist_info['owner']}")
    print(f"Tracks    : {playlist_info['total_tracks']}")

    # --- Tracks ---
    print()
    rows = fetch_all_tracks(sp, playlist_id, market)

    if not rows:
        print("No playable tracks found.")
        sys.exit(0)

    df = pd.DataFrame(rows)

    # Coerce numeric columns for downstream analysis
    df["duration_ms"]  = pd.to_numeric(df["duration_ms"],  errors="coerce").astype("Int64")
    df["popularity"]   = pd.to_numeric(df["popularity"],   errors="coerce").astype("Int64")
    df["track_number"] = pd.to_numeric(df["track_number"], errors="coerce").astype("Int64")
    df["disc_number"]  = pd.to_numeric(df["disc_number"],  errors="coerce").astype("Int64")
    df["added_at"]     = pd.to_datetime(df["added_at"], errors="coerce", utc=True)

    print_summary(df)

    # --- Save ---
    print("\nSaving data...")
    saved = save_dataframe(df, playlist_info, args.output_dir, args.format)

    print(f"\nDone — {len(df)} tracks saved to {len(saved)} file(s).")


if __name__ == "__main__":
    main()
