"""Shared fixtures. pytest.ini sets pythonpath = analyser fetcher, so
`import analyser` → analyser/analyser.py and
`import spotify_fetcher` → fetcher/spotify_fetcher.py.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Representative sample data
# ---------------------------------------------------------------------------

SAMPLE_TRACKS = [
    {
        "track_id": "aaa111",
        "track_name": "Lal Ghagra",
        "isrc": "INV111700001",
        "spotify_url": "https://open.spotify.com/track/aaa111",
        "preview_url": None,
        "is_local": False,
        "artists": "Sahara",
        "artist_ids": "art001",
        "album_name": "Lal Ghagra Single",
        "album_id": "alb001",
        "album_type": "single",
        "album_release_date": "2019-01-01",
        "album_cover_url": None,
        "duration_ms": 219000,
        "duration": "3:39",
        "explicit": False,
        "popularity": 72,
        "track_number": 1,
        "disc_number": 1,
        "market": "IN",
        "added_at": "2020-01-01 00:00:00+00:00",
        "added_by": "user_a",
    },
    {
        "track_id": "bbb222",
        "track_name": "Kangna",
        "isrc": "INV111700002",
        "spotify_url": "https://open.spotify.com/track/bbb222",
        "preview_url": None,
        "is_local": False,
        "artists": "Dr Zeus, Master Rakesh, Deepti, Shortie",
        "artist_ids": "art002, art003, art004, art005",
        "album_name": "Kangna Album",
        "album_id": "alb002",
        "album_type": "album",
        "album_release_date": "2008-06-01",
        "album_cover_url": None,
        "duration_ms": 209000,
        "duration": "3:29",
        "explicit": False,
        "popularity": 65,
        "track_number": 2,
        "disc_number": 1,
        "market": "IN",
        "added_at": "2020-02-01 00:00:00+00:00",
        "added_by": "user_a",
    },
    {
        "track_id": "ccc333",
        "track_name": "Raat Di Gedi",
        "isrc": "INV111700003",
        "spotify_url": "https://open.spotify.com/track/ccc333",
        "preview_url": None,
        "is_local": False,
        "artists": "Diljit Dosanjh",
        "artist_ids": "art006",
        "album_name": "Raat Di Gedi Single",
        "album_id": "alb003",
        "album_type": "single",
        "album_release_date": "2017-10-01",
        "album_cover_url": None,
        "duration_ms": 198000,
        "duration": "3:18",
        "explicit": False,
        "popularity": 80,
        "track_number": 1,
        "disc_number": 1,
        "market": "IN",
        "added_at": "2020-03-01 00:00:00+00:00",
        "added_by": "user_b",
    },
    {
        "track_id": "ddd444",
        "track_name": "High Heels",
        "isrc": "INV111700004",
        "spotify_url": "https://open.spotify.com/track/ddd444",
        "preview_url": None,
        "is_local": False,
        "artists": "Jaz Dhami, Yo Yo Honey Singh",
        "artist_ids": "art007, art008",
        "album_name": "High Heels Single",
        "album_id": "alb004",
        "album_type": "single",
        "album_release_date": "2011-05-01",
        "album_cover_url": None,
        "duration_ms": 297000,
        "duration": "4:57",
        "explicit": False,
        "popularity": 68,
        "track_number": 1,
        "disc_number": 1,
        "market": "IN",
        "added_at": "2020-04-01 00:00:00+00:00",
        "added_by": "user_a",
    },
    {
        "track_id": "eee555",
        "track_name": "Kangna",
        "isrc": "INV111700005",
        "spotify_url": "https://open.spotify.com/track/eee555",
        "preview_url": None,
        "is_local": False,
        "artists": "Dr Zeus, Master Rakesh, Shortie, Deepti",
        "artist_ids": "art002, art003, art005, art004",
        "album_name": "Kangna Re-issue",
        "album_id": "alb005",
        "album_type": "album",
        "album_release_date": "2012-09-01",
        "album_cover_url": None,
        "duration_ms": 211000,
        "duration": "3:31",
        "explicit": False,
        "popularity": 60,
        "track_number": 1,
        "disc_number": 1,
        "market": "IN",
        "added_at": "2020-05-01 00:00:00+00:00",
        "added_by": "user_b",
    },
]


@pytest.fixture(scope="session")
def sample_df() -> pd.DataFrame:
    return pd.DataFrame(SAMPLE_TRACKS)


@pytest.fixture(scope="session")
def data_dir(tmp_path_factory) -> Path:
    """Persistent temp directory for test data files (lives for whole session)."""
    return tmp_path_factory.mktemp("data")


@pytest.fixture(scope="session")
def sample_csv(data_dir, sample_df) -> Path:
    path = data_dir / "sample.csv"
    sample_df.to_csv(path, index=False)
    return path


@pytest.fixture(scope="session")
def sample_json(data_dir, sample_df) -> Path:
    path = data_dir / "sample.json"
    payload = {
        "playlist": {"playlist_name": "Test Playlist"},
        "fetched_at": "2026-01-01T00:00:00Z",
        "tracks": sample_df.to_dict(orient="records"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def sample_parquet(data_dir, sample_df) -> Path:
    path = data_dir / "sample.parquet"
    sample_df.to_parquet(path, index=False, engine="pyarrow")
    return path


@pytest.fixture(scope="session")
def real_csv() -> Path:
    """Returns the first available fetcher CSV in spotify_data/ (alphabetical order).
    Skips if the directory is empty — never written by tests."""
    data_dir = Path(__file__).parent.parent / "spotify_data"
    csvs = sorted(data_dir.glob("*.csv"))
    if not csvs:
        pytest.skip("No CSV files found in spotify_data/ — skipping live-data smoke tests")
    return csvs[0]
