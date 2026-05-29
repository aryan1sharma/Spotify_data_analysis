"""Tests for analyser/analyser.py."""
from pathlib import Path

import pandas as pd
import pytest

import analyser as ana


# ---------------------------------------------------------------------------
# load_data
# ---------------------------------------------------------------------------

class TestLoadData:
    def test_csv(self, sample_csv):
        df = ana.load_data(str(sample_csv))
        assert len(df) == 5
        assert "track_id" in df.columns

    def test_json(self, sample_json):
        df = ana.load_data(str(sample_json))
        assert len(df) == 5
        assert "track_name" in df.columns

    def test_parquet(self, sample_parquet):
        df = ana.load_data(str(sample_parquet))
        assert len(df) == 5

    def test_directory_loads_and_deduplicates(self, data_dir, sample_df):
        # Write two CSVs that share a track_id — directory load should deduplicate
        (data_dir / "dir_a.csv").write_text(
            sample_df.head(3).to_csv(index=False), encoding="utf-8"
        )
        (data_dir / "dir_b.csv").write_text(
            sample_df.tail(3).to_csv(index=False), encoding="utf-8"
        )
        df = ana.load_data(str(data_dir))
        assert df["track_id"].is_unique

    def test_unsupported_extension_exits(self, tmp_path):
        bad = tmp_path / "data.txt"
        bad.write_text("col\nval")
        with pytest.raises(SystemExit):
            ana.load_data(str(bad))

    def test_missing_path_exits(self):
        with pytest.raises(SystemExit):
            ana.load_data("/nonexistent/path/nowhere.csv")

    def test_empty_directory_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            ana.load_data(str(tmp_path))


# ---------------------------------------------------------------------------
# normalise_str
# ---------------------------------------------------------------------------

class TestNormaliseStr:
    def test_strips_whitespace(self):
        assert ana.normalise_str("  hello  ", "field") == "hello"

    def test_empty_exits(self):
        with pytest.raises(SystemExit):
            ana.normalise_str("   ", "field")

    def test_passes_through_normal(self):
        assert ana.normalise_str("abc123", "field") == "abc123"


# ---------------------------------------------------------------------------
# normalise_duration
# ---------------------------------------------------------------------------

class TestNormaliseDuration:
    def test_plain(self):
        assert ana.normalise_duration("3:39") == "3:39"

    def test_leading_zero_minutes(self):
        assert ana.normalise_duration("03:39") == "3:39"

    def test_zero_seconds(self):
        assert ana.normalise_duration("0:00") == "0:00"

    def test_large_minutes(self):
        assert ana.normalise_duration("12:05") == "12:05"

    def test_whitespace_stripped(self):
        assert ana.normalise_duration("  4:57  ") == "4:57"

    def test_missing_colon_exits(self):
        with pytest.raises(SystemExit):
            ana.normalise_duration("339")

    def test_seconds_out_of_range_exits(self):
        with pytest.raises(SystemExit):
            ana.normalise_duration("3:60")

    def test_negative_minutes_exits(self):
        with pytest.raises(SystemExit):
            ana.normalise_duration("-1:30")

    def test_non_numeric_exits(self):
        with pytest.raises(SystemExit):
            ana.normalise_duration("m:ss")


# ---------------------------------------------------------------------------
# normalise_name_list
# ---------------------------------------------------------------------------

class TestNormaliseNameList:
    def test_strips_each(self):
        assert ana.normalise_name_list(["  a  ", " b"], "artists") == ["a", "b"]

    def test_blank_entry_exits(self):
        with pytest.raises(SystemExit):
            ana.normalise_name_list(["valid", "  "], "artists")

    def test_single_entry(self):
        assert ana.normalise_name_list(["Diljit Dosanjh"], "artists") == ["Diljit Dosanjh"]


# ---------------------------------------------------------------------------
# _filter_exact
# ---------------------------------------------------------------------------

class TestFilterExact:
    def test_case_sensitive_hit(self, sample_df):
        result = ana._filter_exact(sample_df, "track_id", "aaa111")
        assert len(result) == 1
        assert result.iloc[0]["track_id"] == "aaa111"

    def test_case_sensitive_miss(self, sample_df):
        result = ana._filter_exact(sample_df, "track_id", "AAA111")
        assert result.empty

    def test_case_insensitive_hit(self, sample_df):
        result = ana._filter_exact(sample_df, "track_name", "KANGNA", ci=True)
        assert len(result) == 2

    def test_case_insensitive_miss(self, sample_df):
        result = ana._filter_exact(sample_df, "track_name", "nonexistent", ci=True)
        assert result.empty

    def test_nan_cell_does_not_match(self):
        df = pd.DataFrame({"col": [None, "value"]})
        result = ana._filter_exact(df, "col", "nan")
        # "nan" as a string should not match missing values
        assert result.empty or all(result["col"].notna())


# ---------------------------------------------------------------------------
# _filter_list_all
# ---------------------------------------------------------------------------

class TestFilterListAll:
    def test_single_artist_ci(self, sample_df):
        result = ana._filter_list_all(sample_df, "artists", ["diljit dosanjh"], ci=True)
        assert len(result) == 1
        assert result.iloc[0]["track_id"] == "ccc333"

    def test_single_artist_case_sensitive_miss(self, sample_df):
        result = ana._filter_list_all(sample_df, "artists", ["diljit dosanjh"], ci=False)
        assert result.empty

    def test_multi_artist_all_present(self, sample_df):
        # Both bbb222 and eee555 have Dr Zeus, Master Rakesh, Shortie, Deepti
        result = ana._filter_list_all(
            sample_df, "artists", ["Dr Zeus", "Shortie"], ci=True
        )
        assert len(result) == 2
        assert set(result["track_id"]) == {"bbb222", "eee555"}

    def test_multi_artist_partial_miss(self, sample_df):
        # "Diljit Dosanjh" and "Sahara" never appear together
        result = ana._filter_list_all(
            sample_df, "artists", ["Diljit Dosanjh", "Sahara"], ci=True
        )
        assert result.empty

    def test_nan_cell_excluded(self):
        df = pd.DataFrame({"artists": [None, "Artist A, Artist B"]})
        result = ana._filter_list_all(df, "artists", ["Artist A"], ci=True)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# apply_filters
# ---------------------------------------------------------------------------

class TestApplyFilters:
    def test_no_filters_returns_all(self, sample_df):
        result = ana.apply_filters(sample_df, {})
        assert len(result) == len(sample_df)

    def test_filter_track_id(self, sample_df):
        result = ana.apply_filters(sample_df, {"track_id": "aaa111"})
        assert list(result["track_id"]) == ["aaa111"]

    def test_filter_track_name_case_insensitive(self, sample_df):
        result = ana.apply_filters(sample_df, {"track_name": "kangna"})
        assert len(result) == 2
        assert set(result["track_id"]) == {"bbb222", "eee555"}

    def test_filter_album_name_case_insensitive(self, sample_df):
        result = ana.apply_filters(sample_df, {"album_name": "kangna album"})
        assert len(result) == 1
        assert result.iloc[0]["track_id"] == "bbb222"

    def test_filter_duration(self, sample_df):
        result = ana.apply_filters(sample_df, {"duration": "3:39"})
        assert list(result["track_id"]) == ["aaa111"]

    def test_filter_added_by(self, sample_df):
        result = ana.apply_filters(sample_df, {"added_by": "user_b"})
        assert set(result["track_id"]) == {"ccc333", "eee555"}

    def test_filter_artists_single(self, sample_df):
        result = ana.apply_filters(sample_df, {"artists": ["Diljit Dosanjh"]})
        assert list(result["track_id"]) == ["ccc333"]

    def test_filter_artists_case_insensitive(self, sample_df):
        result = ana.apply_filters(sample_df, {"artists": ["DILJIT DOSANJH"]})
        assert list(result["track_id"]) == ["ccc333"]

    def test_filter_artists_multi_and(self, sample_df):
        result = ana.apply_filters(sample_df, {"artists": ["Dr Zeus", "Shortie"]})
        assert len(result) == 2

    def test_filter_artist_ids_case_sensitive(self, sample_df):
        # art006 = Diljit Dosanjh; lowercase should NOT match
        result = ana.apply_filters(sample_df, {"artist_ids": ["ART006"]})
        assert result.empty

    def test_multi_filter_and_logic(self, sample_df):
        # track_name=Kangna AND added_by=user_a -> only bbb222
        result = ana.apply_filters(sample_df, {"track_name": "Kangna", "added_by": "user_a"})
        assert list(result["track_id"]) == ["bbb222"]

    def test_index_reset_after_filter(self, sample_df):
        result = ana.apply_filters(sample_df, {"track_id": "ccc333"})
        assert list(result.index) == [0]

    def test_no_match_returns_empty(self, sample_df):
        result = ana.apply_filters(sample_df, {"track_id": "zzz999"})
        assert result.empty


# ---------------------------------------------------------------------------
# sort_results
# ---------------------------------------------------------------------------

class TestSortResults:
    def test_sort_by_none_returns_original_order(self, sample_df):
        result = ana.sort_results(sample_df, None, "asc")
        assert list(result["track_id"]) == list(sample_df["track_id"])

    def test_sort_track_name_asc(self, sample_df):
        result = ana.sort_results(sample_df, "track_name", "asc")
        # alphabetical: High Heels < Kangna < Lal Ghagra < Raat Di Gedi
        assert result.iloc[0]["track_id"] == "ddd444"
        assert result.iloc[-1]["track_id"] == "ccc333"

    def test_sort_track_name_desc(self, sample_df):
        result = ana.sort_results(sample_df, "track_name", "desc")
        # Raat Di Gedi > Lal Ghagra > Kangna > High Heels
        assert result.iloc[0]["track_id"] == "ccc333"
        assert result.iloc[-1]["track_id"] == "ddd444"

    def test_sort_artists_asc(self, sample_df):
        result = ana.sort_results(sample_df, "artists", "asc")
        # "Diljit Dosanjh" (D) first, "Sahara" (S) last
        assert result.iloc[0]["track_id"] == "ccc333"
        assert result.iloc[-1]["track_id"] == "aaa111"

    def test_sort_album_name_asc(self, sample_df):
        result = ana.sort_results(sample_df, "album_name", "asc")
        # "High Heels Single" first, "Raat Di Gedi Single" last
        assert result.iloc[0]["track_id"] == "ddd444"
        assert result.iloc[-1]["track_id"] == "ccc333"

    def test_sort_added_by_asc(self, sample_df):
        result = ana.sort_results(sample_df, "added_by", "asc")
        # user_a < user_b
        assert result.iloc[0]["added_by"] == "user_a"
        assert result.iloc[-1]["added_by"] == "user_b"

    def test_sort_alphabetical_case_insensitive(self):
        # "banana" < "Cherry" case-insensitively,
        # but "Cherry" < "banana" case-sensitively (uppercase C < lowercase b in ASCII)
        df = pd.DataFrame({
            "track_name": ["banana", "Cherry", "apple"],
            "track_id":   ["t1",     "t2",     "t3"],
        })
        result = ana.sort_results(df, "track_name", "asc")
        assert list(result["track_name"]) == ["apple", "banana", "Cherry"]

    def test_sort_duration_asc(self, sample_df):
        result = ana.sort_results(sample_df, "duration", "asc")
        # 3:18(ccc333) < 3:29(bbb222) < 3:31(eee555) < 3:39(aaa111) < 4:57(ddd444)
        assert list(result["track_id"]) == ["ccc333", "bbb222", "eee555", "aaa111", "ddd444"]

    def test_sort_duration_desc(self, sample_df):
        result = ana.sort_results(sample_df, "duration", "desc")
        # 4:57 > 3:39 > 3:31 > 3:29 > 3:18
        assert list(result["track_id"]) == ["ddd444", "aaa111", "eee555", "bbb222", "ccc333"]

    def test_sort_album_release_date_asc(self, sample_df):
        result = ana.sort_results(sample_df, "album_release_date", "asc")
        # 2008-06(bbb222) < 2011-05(ddd444) < 2012-09(eee555) < 2017-10(ccc333) < 2019-01(aaa111)
        assert list(result["track_id"]) == ["bbb222", "ddd444", "eee555", "ccc333", "aaa111"]

    def test_sort_album_release_date_desc(self, sample_df):
        result = ana.sort_results(sample_df, "album_release_date", "desc")
        assert list(result["track_id"]) == ["aaa111", "ccc333", "eee555", "ddd444", "bbb222"]

    def test_sort_album_release_date_year_only_asc(self):
        # Year-only strings must sort chronologically, not fall to na_position=last
        df = pd.DataFrame({
            "album_release_date": ["2019", "2008", "2011"],
            "track_id":           ["t1",   "t2",   "t3"],
        })
        result = ana.sort_results(df, "album_release_date", "asc")
        assert list(result["track_id"]) == ["t2", "t3", "t1"]  # 2008 < 2011 < 2019

    def test_sort_album_release_date_mixed_precision_asc(self):
        # YYYY, YYYY-MM, and YYYY-MM-DD can coexist and sort correctly
        df = pd.DataFrame({
            "album_release_date": ["2012-09-01", "2008",  "2011-05"],
            "track_id":           ["t1",         "t2",    "t3"],
        })
        result = ana.sort_results(df, "album_release_date", "asc")
        assert list(result["track_id"]) == ["t2", "t3", "t1"]  # 2008 < 2011-05 < 2012-09-01

    def test_sort_album_release_date_zero_year_sorts_first(self):
        # "0000" expands to "0000-01-01" and sorts before all real dates ascending
        df = pd.DataFrame({
            "album_release_date": ["2019-01-01", "0000", "2008-01-01"],
            "track_id":           ["t1",         "t_zero", "t2"],
        })
        result = ana.sort_results(df, "album_release_date", "asc")
        assert result.iloc[0]["track_id"] == "t_zero"  # year 0000 < 2008 < 2019

    def test_sort_added_at_asc(self, sample_df):
        result = ana.sort_results(sample_df, "added_at", "asc")
        # sample data is already in ascending added_at order
        assert list(result["track_id"]) == ["aaa111", "bbb222", "ccc333", "ddd444", "eee555"]

    def test_sort_added_at_desc(self, sample_df):
        result = ana.sort_results(sample_df, "added_at", "desc")
        assert list(result["track_id"]) == ["eee555", "ddd444", "ccc333", "bbb222", "aaa111"]

    def test_invalid_sort_column_exits(self, sample_df):
        with pytest.raises(SystemExit):
            ana.sort_results(sample_df, "nonexistent_column", "asc")


# ---------------------------------------------------------------------------
# resolve_columns
# ---------------------------------------------------------------------------

class TestResolveColumns:
    def test_none_returns_all(self, sample_df):
        cols = ana.resolve_columns(sample_df, None)
        assert cols == list(sample_df.columns)

    def test_empty_list_returns_all(self, sample_df):
        cols = ana.resolve_columns(sample_df, [])
        assert cols == list(sample_df.columns)

    def test_valid_subset(self, sample_df):
        cols = ana.resolve_columns(sample_df, ["track_name", "artists"])
        assert cols == ["track_name", "artists"]

    def test_unknown_column_exits(self, sample_df):
        with pytest.raises(SystemExit):
            ana.resolve_columns(sample_df, ["nonexistent_col"])


# ---------------------------------------------------------------------------
# list_columns
# ---------------------------------------------------------------------------

class TestListColumns:
    def test_prints_all_columns(self, sample_df, capsys):
        ana.list_columns(sample_df)
        out = capsys.readouterr().out
        for col in sample_df.columns:
            assert col in out


# ---------------------------------------------------------------------------
# display_results
# ---------------------------------------------------------------------------

class TestDisplayResults:
    def test_with_results(self, sample_df, capsys):
        filters = {"track_name": "Kangna"}
        cols = list(sample_df.columns)
        ana.display_results(sample_df[sample_df["track_name"] == "Kangna"].reset_index(drop=True), filters, cols)
        out = capsys.readouterr().out
        assert "Kangna" in out

    def test_empty_results(self, capsys):
        empty = pd.DataFrame(columns=["track_name"])
        ana.display_results(empty, {"track_name": "Ghost"}, ["track_name"])
        out = capsys.readouterr().out
        assert "No tracks found" in out

    def test_single_track_label(self, sample_df, capsys):
        single = sample_df.head(1).reset_index(drop=True)
        ana.display_results(single, {"track_id": "aaa111"}, list(sample_df.columns))
        out = capsys.readouterr().out
        assert "1 track matching" in out

    def test_no_filter_label(self, sample_df, capsys):
        # Empty summary (no filters) → "N tracks:" without "matching"
        ana.display_results(sample_df, {}, list(sample_df.columns))
        out = capsys.readouterr().out
        assert "tracks:" in out
        assert "matching" not in out

    def test_no_filter_empty_message(self, capsys):
        # Empty summary + empty df → "No tracks found." without "matching"
        empty = pd.DataFrame(columns=["track_name"])
        ana.display_results(empty, {}, ["track_name"])
        out = capsys.readouterr().out
        assert "No tracks found." in out
        assert "matching" not in out


# ---------------------------------------------------------------------------
# save_results
# ---------------------------------------------------------------------------

class TestSaveResults:
    def test_creates_output_dir(self, sample_df, tmp_path):
        new_dir = tmp_path / "nested" / "output"
        ana.save_results(sample_df, list(sample_df.columns), str(new_dir))
        assert new_dir.exists()

    def test_saves_csv_file(self, sample_df, tmp_path):
        ana.save_results(sample_df, list(sample_df.columns), str(tmp_path))
        assert len(list(tmp_path.glob("*.csv"))) == 1

    def test_filename_matches_pattern(self, sample_df, tmp_path):
        import re
        ana.save_results(sample_df, list(sample_df.columns), str(tmp_path))
        name = list(tmp_path.glob("*.csv"))[0].name
        assert re.match(r"analysis_\d{8}_\d{6}\.csv", name)

    def test_saved_csv_contains_correct_columns(self, sample_df, tmp_path):
        cols = ["track_id", "track_name"]
        ana.save_results(sample_df, cols, str(tmp_path))
        saved = pd.read_csv(list(tmp_path.glob("*.csv"))[0])
        assert list(saved.columns) == cols

    def test_saved_csv_contains_all_rows(self, sample_df, tmp_path):
        ana.save_results(sample_df, list(sample_df.columns), str(tmp_path))
        saved = pd.read_csv(list(tmp_path.glob("*.csv"))[0])
        assert len(saved) == len(sample_df)

    def test_returns_saved_path(self, sample_df, tmp_path):
        path = ana.save_results(sample_df, list(sample_df.columns), str(tmp_path))
        assert Path(path).exists()

    def test_save_flag_off_by_default(self):
        args = ana.build_parser().parse_args(["--file", "x.csv"])
        assert args.save is False

    def test_save_flag_can_be_set(self):
        args = ana.build_parser().parse_args(["--file", "x.csv", "--save"])
        assert args.save is True


# ---------------------------------------------------------------------------
# Real-CSV smoke test
# ---------------------------------------------------------------------------

class TestRealCSV:
    def test_loads_real_csv(self, real_csv):
        df = ana.load_data(str(real_csv))
        assert len(df) > 0
        assert "track_id" in df.columns

    def test_real_csv_duration_filter(self, real_csv):
        df = ana.load_data(str(real_csv))
        sample_duration = df["duration"].dropna().iloc[0]
        result = ana.apply_filters(df, {"duration": sample_duration})
        assert len(result) >= 1
        assert all(result["duration"] == sample_duration)

    def test_real_csv_added_by_filter(self, real_csv):
        df = ana.load_data(str(real_csv))
        user_id = df["added_by"].dropna().iloc[0]
        result = ana.apply_filters(df, {"added_by": user_id})
        assert len(result) >= 1
        assert all(result["added_by"] == user_id)
