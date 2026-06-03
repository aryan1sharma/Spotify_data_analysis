"""Tests for fetcher/spotify_fetcher.py — pure-function and mocked-API coverage."""
import datetime
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

import spotify_fetcher as sf


# ---------------------------------------------------------------------------
# _ms_to_mmss
# ---------------------------------------------------------------------------

class TestMsToMmss:
    def test_exact_minutes(self):
        assert sf._ms_to_mmss(180_000) == "3:00"

    def test_with_seconds(self):
        assert sf._ms_to_mmss(219_000) == "3:39"

    def test_zero(self):
        assert sf._ms_to_mmss(0) == "0:00"

    def test_single_digit_seconds_padded(self):
        assert sf._ms_to_mmss(61_000) == "1:01"

    def test_large_duration(self):
        assert sf._ms_to_mmss(3_600_000) == "60:00"


# ---------------------------------------------------------------------------
# _extract_track
# ---------------------------------------------------------------------------

class TestExtractTrack:
    def _item(self, overrides=None):
        """Minimal valid playlist-item dict."""
        base = {
            "added_at": "2024-01-01T00:00:00Z",
            "added_by": {"id": "user123"},
            "item": {
                "id": "tid001",
                "name": "Test Track",
                "type": "track",
                "duration_ms": 219000,
                "explicit": False,
                "popularity": 70,
                "track_number": 1,
                "disc_number": 1,
                "is_local": False,
                "preview_url": None,
                "external_ids": {"isrc": "TEST123"},
                "external_urls": {"spotify": "https://open.spotify.com/track/tid001"},
                "artists": [{"id": "art001", "name": "Artist A"}],
                "album": {
                    "id": "alb001",
                    "name": "Test Album",
                    "album_type": "single",
                    "release_date": "2024-01-01",
                    "images": [{"url": "https://example.com/cover.jpg"}],
                },
            },
        }
        if overrides:
            base.update(overrides)
        return base

    def test_returns_dict_for_valid_item(self):
        row = sf._extract_track(self._item(), "US")
        assert row is not None
        assert row["track_id"] == "tid001"
        assert row["track_name"] == "Test Track"
        assert row["duration"] == "3:39"
        assert row["isrc"] == "TEST123"
        assert row["added_by"] == "user123"
        assert row["market"] == "US"

    def test_multi_artist_comma_joined(self):
        item = self._item()
        item["item"]["artists"] = [
            {"id": "a1", "name": "Artist A"},
            {"id": "a2", "name": "Artist B"},
        ]
        row = sf._extract_track(item, "US")
        assert row["artists"] == "Artist A, Artist B"
        assert row["artist_ids"] == "a1, a2"

    def test_skips_podcast_episode(self):
        item = self._item()
        item["item"]["type"] = "episode"
        assert sf._extract_track(item, "US") is None

    def test_skips_null_item(self):
        assert sf._extract_track({"added_at": "x", "added_by": {"id": "u"}}, "US") is None

    def test_no_images_sets_none(self):
        item = self._item()
        item["item"]["album"]["images"] = []
        row = sf._extract_track(item, "US")
        assert row["album_cover_url"] is None

    def test_uses_deprecated_track_key_as_fallback(self):
        """Spotify used to put the track under 'track' key; must still work."""
        item = self._item()
        track = item.pop("item")
        item["track"] = track
        row = sf._extract_track(item, "US")
        assert row is not None
        assert row["track_id"] == "tid001"

    def test_artist_without_id_excluded_from_artist_ids(self):
        item = self._item()
        item["item"]["artists"] = [
            {"id": None, "name": "Local Artist"},
            {"id": "a2", "name": "Real Artist"},
        ]
        row = sf._extract_track(item, "US")
        assert "Local Artist" in row["artists"]
        assert "a2" in row["artist_ids"]
        assert "None" not in row["artist_ids"]


# ---------------------------------------------------------------------------
# fetch_playlist_info (mocked)
# ---------------------------------------------------------------------------

class TestFetchPlaylistInfo:
    def _mock_sp(self):
        sp = MagicMock()
        sp.playlist.return_value = {
            "id": "pl001",
            "name": "My Playlist",
            "description": "A test playlist",
            "public": True,
            "collaborative": False,
            "owner": {"display_name": "Test User", "id": "user001"},
            "followers": {"total": 42},
            "images": [{"url": "https://example.com/cover.jpg"}],
            "tracks": {"total": 100},
        }
        return sp

    def test_returns_expected_keys(self):
        sp = self._mock_sp()
        info = sf.fetch_playlist_info(sp, "pl001", "US")
        assert info["playlist_id"] == "pl001"
        assert info["playlist_name"] == "My Playlist"
        assert info["owner"] == "Test User"
        assert info["owner_id"] == "user001"
        assert info["total_tracks"] == 100
        assert info["followers"] == 42
        assert info["cover_image"] == "https://example.com/cover.jpg"

    def test_no_images_sets_none(self):
        sp = self._mock_sp()
        sp.playlist.return_value["images"] = []
        info = sf.fetch_playlist_info(sp, "pl001", "US")
        assert info["cover_image"] is None

    def test_calls_correct_playlist_id(self):
        sp = self._mock_sp()
        sf.fetch_playlist_info(sp, "pl001", "US")
        sp.playlist.assert_called_once()
        call_args = sp.playlist.call_args
        assert call_args[0][0] == "pl001"


# ---------------------------------------------------------------------------
# fetch_all_tracks (mocked)
# ---------------------------------------------------------------------------

class TestFetchAllTracks:
    def _make_item(self, track_id: str, name: str) -> dict:
        return {
            "added_at": "2024-01-01T00:00:00Z",
            "added_by": {"id": "user1"},
            "item": {
                "id": track_id,
                "name": name,
                "type": "track",
                "duration_ms": 180_000,
                "explicit": False,
                "popularity": 50,
                "track_number": 1,
                "disc_number": 1,
                "is_local": False,
                "preview_url": None,
                "external_ids": {},
                "external_urls": {},
                "artists": [{"id": "a1", "name": "Artist"}],
                "album": {
                    "id": "alb1", "name": "Album", "album_type": "album",
                    "release_date": "2024-01-01", "images": [],
                },
            },
        }

    def test_single_page(self):
        sp = MagicMock()
        sp.playlist_items.return_value = {
            "items": [self._make_item("t1", "Track 1"), self._make_item("t2", "Track 2")],
            "total": 2,
            "next": None,
        }
        rows = sf.fetch_all_tracks(sp, "pl001", "US")
        assert len(rows) == 2
        assert rows[0]["track_id"] == "t1"
        assert rows[1]["track_id"] == "t2"

    def test_pagination_two_pages(self):
        sp = MagicMock()
        page1 = {
            "items": [self._make_item(f"t{i}", f"Track {i}") for i in range(100)],
            "total": 150,
            "next": "next_url",
        }
        page2 = {
            "items": [self._make_item(f"t{i}", f"Track {i}") for i in range(100, 150)],
            "total": 150,
            "next": None,
        }
        sp.playlist_items.side_effect = [page1, page2]
        rows = sf.fetch_all_tracks(sp, "pl001", "US")
        assert len(rows) == 150
        assert sp.playlist_items.call_count == 2

    def test_skips_null_items(self):
        sp = MagicMock()
        null_item = {"added_at": "x", "added_by": {"id": "u"}}
        sp.playlist_items.return_value = {
            "items": [null_item, self._make_item("t1", "Track 1")],
            "total": 2,
            "next": None,
        }
        rows = sf.fetch_all_tracks(sp, "pl001", "US")
        assert len(rows) == 1
        assert rows[0]["track_id"] == "t1"

    def test_skips_podcast_episodes(self):
        sp = MagicMock()
        episode_item = {
            "added_at": "x",
            "added_by": {"id": "u"},
            "item": {"type": "episode", "id": "ep1", "name": "Ep"},
        }
        sp.playlist_items.return_value = {
            "items": [episode_item],
            "total": 1,
            "next": None,
        }
        rows = sf.fetch_all_tracks(sp, "pl001", "US")
        assert len(rows) == 0


# ---------------------------------------------------------------------------
# save_dataframe
# ---------------------------------------------------------------------------

class TestSaveDataframe:
    @pytest.fixture
    def sample_df(self):
        return pd.DataFrame([
            {
                "track_id": "t1", "track_name": "Song", "duration_ms": 180000,
                "duration": "3:00", "artists": "Artist", "album_name": "Album",
                "popularity": 50,
            }
        ])

    @pytest.fixture
    def playlist_info(self):
        return {"playlist_name": "Test Playlist"}

    def test_saves_csv(self, tmp_path, sample_df, playlist_info):
        saved = sf.save_dataframe(sample_df, playlist_info, str(tmp_path), ["csv"])
        assert len(saved) == 1
        assert saved[0].endswith(".csv")
        loaded = pd.read_csv(saved[0])
        assert "track_id" in loaded.columns

    def test_saves_json(self, tmp_path, sample_df, playlist_info):
        saved = sf.save_dataframe(sample_df, playlist_info, str(tmp_path), ["json"])
        assert len(saved) == 1
        data = json.loads(open(saved[0]).read())
        assert "tracks" in data
        assert data["tracks"][0]["track_id"] == "t1"

    def test_saves_parquet(self, tmp_path, sample_df, playlist_info):
        saved = sf.save_dataframe(sample_df, playlist_info, str(tmp_path), ["parquet"])
        assert len(saved) == 1
        loaded = pd.read_parquet(saved[0])
        assert loaded.iloc[0]["track_id"] == "t1"

    def test_saves_multiple_formats(self, tmp_path, sample_df, playlist_info):
        saved = sf.save_dataframe(sample_df, playlist_info, str(tmp_path), ["csv", "json", "parquet"])
        assert len(saved) == 3

    def test_creates_output_dir(self, tmp_path, sample_df, playlist_info):
        new_dir = tmp_path / "nested" / "output"
        sf.save_dataframe(sample_df, playlist_info, str(new_dir), ["csv"])
        assert new_dir.exists()

    def test_filename_sanitises_special_chars(self, tmp_path, sample_df):
        info = {"playlist_name": "My/Playlist: Special!"}
        saved = sf.save_dataframe(sample_df, info, str(tmp_path), ["csv"])
        # No slashes or colons in the filename
        name = Path(saved[0]).name
        assert "/" not in name
        assert ":" not in name

    def test_none_playlist_name_falls_back(self, tmp_path, sample_df):
        info = {"playlist_name": None}
        saved = sf.save_dataframe(sample_df, info, str(tmp_path), ["csv"])
        assert len(saved) == 1


# ---------------------------------------------------------------------------
# parse_playlist_id
# ---------------------------------------------------------------------------

class TestParsePlaylistId:
    def test_bare_id_passthrough(self):
        assert sf.parse_playlist_id("37i9dQZF1DXcBWIGoYBM5M") == "37i9dQZF1DXcBWIGoYBM5M"

    def test_strips_whitespace(self):
        assert sf.parse_playlist_id("  37i9dQZF1DXcBWIGoYBM5M  ") == "37i9dQZF1DXcBWIGoYBM5M"

    def test_open_spotify_url(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"
        assert sf.parse_playlist_id(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_open_spotify_url_no_query(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        assert sf.parse_playlist_id(url) == "37i9dQZF1DXcBWIGoYBM5M"

    def test_spotify_uri(self):
        uri = "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
        assert sf.parse_playlist_id(uri) == "37i9dQZF1DXcBWIGoYBM5M"


# ---------------------------------------------------------------------------
# Multi-playlist: build_parser + main
# ---------------------------------------------------------------------------

# Minimal track row that survives all dtype coercions in main()
_MINIMAL_TRACK = {
    "track_id": "t1", "track_name": "Song", "isrc": None,
    "spotify_url": None, "preview_url": None, "is_local": False,
    "artists": "Artist", "artist_ids": "a1",
    "album_name": "Album", "album_id": "alb1", "album_type": "single",
    "album_release_date": "2024-01-01", "album_cover_url": None,
    "duration_ms": 180000, "duration": "3:00",
    "explicit": False, "popularity": 50,
    "track_number": 1, "disc_number": 1,
    "market": "US", "added_at": "2024-01-01T00:00:00Z", "added_by": "user1",
}

def _playlist_info(pid: str, name: str) -> dict:
    return {
        "playlist_id": pid, "playlist_name": name,
        "description": "", "owner": "user", "owner_id": "user",
        "public": True, "collaborative": False,
        "followers": 0, "total_tracks": 1, "cover_image": None,
    }

def _mock_sp() -> MagicMock:
    sp = MagicMock()
    sp.current_user.return_value = {
        "display_name": "Test User", "email": "t@test.com", "country": "US",
    }
    return sp


class TestMultiPlaylist:
    def test_parser_accepts_multiple_playlists(self):
        args = sf.build_parser().parse_args(["--playlist", "id1", "id2", "id3"])
        assert args.playlist == ["id1", "id2", "id3"]

    def test_parser_single_playlist_still_works(self):
        args = sf.build_parser().parse_args(["--playlist", "id1"])
        assert args.playlist == ["id1"]

    def test_main_calls_save_once_per_playlist(self, tmp_path):
        with patch("sys.argv", ["sf", "--playlist", "pl1", "pl2", "--output-dir", str(tmp_path)]), \
             patch("spotify_fetcher.build_spotify_client", return_value=_mock_sp()), \
             patch("spotify_fetcher.fetch_playlist_info", side_effect=[
                 _playlist_info("pl1", "Playlist 1"),
                 _playlist_info("pl2", "Playlist 2"),
             ]), \
             patch("spotify_fetcher.fetch_all_tracks", return_value=[_MINIMAL_TRACK]), \
             patch("spotify_fetcher.print_summary"), \
             patch("spotify_fetcher.save_dataframe", return_value=[]) as mock_save:
            sf.main()
        assert mock_save.call_count == 2

    def test_main_passes_correct_playlist_id_to_fetch(self, tmp_path):
        # Verifies each playlist's ID is passed through parse_playlist_id correctly
        with patch("sys.argv", ["sf", "--playlist", "pl1", "pl2", "--output-dir", str(tmp_path)]), \
             patch("spotify_fetcher.build_spotify_client", return_value=_mock_sp()), \
             patch("spotify_fetcher.fetch_playlist_info", side_effect=[
                 _playlist_info("pl1", "P1"), _playlist_info("pl2", "P2"),
             ]) as mock_info, \
             patch("spotify_fetcher.fetch_all_tracks", return_value=[_MINIMAL_TRACK]), \
             patch("spotify_fetcher.print_summary"), \
             patch("spotify_fetcher.save_dataframe", return_value=[]):
            sf.main()
        called_ids = [call.args[1] for call in mock_info.call_args_list]
        assert called_ids == ["pl1", "pl2"]

    def test_main_skips_empty_playlist_and_continues(self, tmp_path):
        # Empty playlist (no rows) should be skipped; next playlist still processed
        with patch("sys.argv", ["sf", "--playlist", "empty", "pl2", "--output-dir", str(tmp_path)]), \
             patch("spotify_fetcher.build_spotify_client", return_value=_mock_sp()), \
             patch("spotify_fetcher.fetch_playlist_info", side_effect=[
                 _playlist_info("empty", "Empty Playlist"),
                 _playlist_info("pl2", "Playlist 2"),
             ]), \
             patch("spotify_fetcher.fetch_all_tracks", side_effect=[[], [_MINIMAL_TRACK]]), \
             patch("spotify_fetcher.print_summary"), \
             patch("spotify_fetcher.save_dataframe", return_value=[]) as mock_save:
            sf.main()
        assert mock_save.call_count == 1  # only the non-empty playlist saved


# ---------------------------------------------------------------------------
# Real-CSV smoke test
# ---------------------------------------------------------------------------

class TestRealCSVFetcher:
    def test_real_csv_loads_as_dataframe(self, real_csv):
        df = pd.read_csv(real_csv)
        assert len(df) > 0
        expected_cols = {
            "track_id", "track_name", "duration", "artists",
            "album_name", "added_by", "popularity",
        }
        assert expected_cols.issubset(set(df.columns))

    def test_real_csv_duration_format(self, real_csv):
        df = pd.read_csv(real_csv)
        # All durations should match M:SS format
        pattern = re.compile(r"^\d+:\d{2}$")
        for val in df["duration"].dropna():
            assert pattern.match(str(val)), f"Unexpected duration format: {val!r}"

    def test_real_csv_track_ids_unique(self, real_csv):
        df = pd.read_csv(real_csv)
        assert df["track_id"].is_unique
