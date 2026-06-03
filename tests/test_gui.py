"""Tests for gui/logic.py. gui/app.py (Tkinter shell) is not unit-tested."""
import sys
from pathlib import Path

import pandas as pd
import pytest

import logic


class TestDiscoverCsvs:
    def test_returns_empty_list_when_dir_missing(self, tmp_path):
        assert logic.discover_csvs(tmp_path / "nonexistent") == []

    def test_returns_empty_list_when_no_csvs(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hello")
        assert logic.discover_csvs(tmp_path) == []

    def test_returns_sorted_paths(self, tmp_path):
        for name in ("c.csv", "a.csv", "b.csv"):
            (tmp_path / name).write_text("x")
        assert logic.discover_csvs(tmp_path) == sorted(tmp_path.glob("*.csv"))

    def test_ignores_non_csv_files(self, tmp_path):
        (tmp_path / "data.csv").write_text("x")
        (tmp_path / "data.json").write_text("{}")
        (tmp_path / "data.parquet").write_text("x")
        result = logic.discover_csvs(tmp_path)
        assert len(result) == 1 and result[0].suffix == ".csv"

    def test_returns_path_objects(self, tmp_path):
        (tmp_path / "test.csv").write_text("x")
        assert all(isinstance(p, Path) for p in logic.discover_csvs(tmp_path))


class TestBuildFetcherCommand:
    def test_returns_none_when_no_playlist_ids(self):
        assert logic.build_fetcher_command([], "IN", ["csv"]) is None

    def test_returns_none_when_playlist_ids_all_blank(self):
        assert logic.build_fetcher_command(["", "  "], "IN", ["csv"]) is None

    def test_single_playlist(self):
        cmd = logic.build_fetcher_command(["abc123"], "IN", ["csv"])
        assert cmd is not None and "--playlist" in cmd and "abc123" in cmd

    def test_multiple_playlists(self):
        cmd = logic.build_fetcher_command(["abc123", "def456"], "IN", ["csv"])
        rest = cmd[cmd.index("--playlist") + 1:]
        assert "abc123" in rest and "def456" in rest

    def test_uses_sys_executable(self):
        assert logic.build_fetcher_command(["abc123"], "IN", ["csv"])[0] == sys.executable

    def test_includes_unbuffered_flag(self):
        assert "-u" in logic.build_fetcher_command(["abc123"], "IN", ["csv"])

    def test_includes_market_when_provided(self):
        cmd = logic.build_fetcher_command(["abc123"], "IN", ["csv"])
        assert "--market" in cmd and "IN" in cmd

    def test_omits_market_when_blank(self):
        assert "--market" not in logic.build_fetcher_command(["abc123"], "", ["csv"])

    def test_includes_format_flags(self):
        cmd = logic.build_fetcher_command(["abc123"], "IN", ["csv", "json"])
        rest = cmd[cmd.index("--format") + 1:]
        assert "csv" in rest and "json" in rest

    def test_strips_blank_playlist_ids(self):
        cmd = logic.build_fetcher_command(["abc123", "  ", "def456"], "IN", ["csv"])
        assert "  " not in cmd and "" not in cmd


class TestBuildFilterDict:
    def test_empty_strings_become_none(self):
        result = logic.build_filter_dict({"track_name": "", "artists": "", "duration": ""})
        assert all(v is None for v in result.values())

    def test_whitespace_only_becomes_none(self):
        assert logic.build_filter_dict({"track_name": "   "})["track_name"] is None

    def test_non_empty_strings_preserved(self):
        result = logic.build_filter_dict({"track_name": "Kangna", "duration": "3:29"})
        assert result["track_name"] == "Kangna" and result["duration"] == "3:29"

    def test_artists_split_by_comma_into_list(self):
        assert logic.build_filter_dict({"artists": "Dr Zeus, Shortie"})["artists"] == ["Dr Zeus", "Shortie"]

    def test_artist_ids_split_by_comma_into_list(self):
        assert logic.build_filter_dict({"artist_ids": "art001, art002"})["artist_ids"] == ["art001", "art002"]

    def test_single_artist_still_returns_list(self):
        assert logic.build_filter_dict({"artists": "Diljit Dosanjh"})["artists"] == ["Diljit Dosanjh"]

    def test_blank_artist_field_returns_none(self):
        assert logic.build_filter_dict({"artists": ""})["artists"] is None

    def test_unknown_keys_passed_through(self):
        assert logic.build_filter_dict({"album_id": "alb001"})["album_id"] == "alb001"


class TestDataframeToTreeviewRows:
    def _make_df(self):
        return pd.DataFrame({
            "track_name": ["Song A", "Song B"],
            "artists":    ["Artist 1", "Artist 2"],
            "duration":   ["3:30", "4:00"],
        })

    def test_columns_match_df_columns(self):
        cols, _ = logic.dataframe_to_treeview_rows(self._make_df())
        assert cols == ["track_name", "artists", "duration"]

    def test_row_count_matches_df(self):
        _, rows = logic.dataframe_to_treeview_rows(self._make_df())
        assert len(rows) == 2

    def test_each_row_is_tuple(self):
        _, rows = logic.dataframe_to_treeview_rows(self._make_df())
        assert all(isinstance(r, tuple) for r in rows)

    def test_row_length_matches_columns(self):
        cols, rows = logic.dataframe_to_treeview_rows(self._make_df())
        assert all(len(r) == len(cols) for r in rows)

    def test_nan_replaced_with_empty_string(self):
        df = pd.DataFrame({"a": [1.0, float("nan")], "b": ["x", "y"]})
        _, rows = logic.dataframe_to_treeview_rows(df)
        assert rows[1][0] == ""

    def test_values_are_strings(self):
        df = pd.DataFrame({"n": [1, 2], "s": ["a", "b"]})
        _, rows = logic.dataframe_to_treeview_rows(df)
        assert all(isinstance(v, str) for row in rows for v in row)

    def test_empty_dataframe_returns_empty_rows(self):
        cols, rows = logic.dataframe_to_treeview_rows(pd.DataFrame({"a": [], "b": []}))
        assert cols == ["a", "b"] and rows == []
