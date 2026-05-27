"""Unit tests for SofaScore match collector long/wide helpers (no network)."""

import pandas as pd

from collect_data.collectors.sofascore_matches import (
    _parquet_safe_frame,
    _sofascore_player_match_full,
    _sofascore_team_stats_long,
)


def test_team_stats_long_adds_metadata():
    tdf = pd.DataFrame(
        [
            {
                "period": "ALL",
                "group": "Match overview",
                "key": "expectedGoals",
                "name": "Expected goals",
                "homeValue": 1.2,
                "awayValue": 0.8,
            },
            {
                "period": "1ST",
                "group": "Match overview",
                "key": "ballPossession",
                "name": "Ball possession",
                "homeValue": 55,
                "awayValue": 45,
            },
        ]
    )
    out = _sofascore_team_stats_long(tdf, 99, "Home FC", "Away FC", "Test League", "2024-2025")
    assert len(out) == 2
    assert set(out["match_id"].tolist()) == {99}
    assert out.iloc[0]["home_team"] == "Home FC"
    assert out.iloc[0]["key"] == "expectedGoals"


def test_player_match_full_keeps_api_columns():
    pm = pd.DataFrame(
        [
            {
                "id": 1,
                "name": "Alice",
                "teamId": 10,
                "teamName": "Home FC",
                "minutesPlayed": 90,
                "keyPass": 3,
                "totalTackle": 2,
            }
        ]
    )
    out = _sofascore_player_match_full(pm, 99, 10, "Test League", "2024-2025")
    assert "keyPass" in out.columns
    assert "totalTackle" in out.columns
    assert out.iloc[0]["player_id"] == 1
    assert out.iloc[0]["player_name"] == "Alice"
    assert bool(out.iloc[0]["is_home"]) is True


def test_parquet_safe_frame_jsonifies_nested():
    df = pd.DataFrame([{"meta": {"a": 1}, "tags": [1, 2]}])
    out = _parquet_safe_frame(df)
    assert out.iloc[0]["meta"] == '{"a": 1}'
    assert out.iloc[0]["tags"] == "[1, 2]"
