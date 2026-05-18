"""Tests for DuckDB layer and new MCP tools."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from soccer_server import db
from soccer_server.registry import TOOLS
from soccer_server.tools import (
    tool_data_status,
    tool_get_player,
    tool_get_player_match_log,
    tool_search_matches,
)


@pytest.fixture(scope="module")
def duckdb_ready() -> None:
    """Initialize DuckDB once for the module if local data exists."""
    db.init_db(force=True)


def test_init_db_registers_unified(duckdb_ready: None) -> None:
    n = db.query_scalar("SELECT count(*) FROM unified_prepared")
    assert n is not None
    assert int(n) >= 0


def test_player_match_log_salah(duckdb_ready: None) -> None:
    if db.table_empty("player_match_log"):
        pytest.skip("No SofaScore match player data")
    out = tool_get_player_match_log(
        {"name": "Mohamed Salah", "season": "2024-2025", "limit": 5}
    )
    assert "match_log" in out
    assert out["matches_returned"] > 0


def test_search_matches_epl(duckdb_ready: None) -> None:
    if db.table_empty("match_index"):
        pytest.skip("No match index")
    out = tool_search_matches(
        {"league": "England Premier League", "season": "2024-2025", "limit": 5}
    )
    assert out["count"] > 0
    assert len(out["matches"]) <= 5


@patch("soccer_server.tools.get_backend")
@patch("soccer_server.tools.get_unified")
def test_tool_data_status_includes_duckdb_fields(
    mock_get_unified: MagicMock,
    mock_get_backend: MagicMock,
) -> None:
    class _FakeBackend:
        def list_raw_glob(self, pattern: str) -> list[str]:
            return []

        def exists_rel(self, rel_path: str) -> bool:
            return False

    mock_get_backend.return_value = _FakeBackend()
    mock_get_unified.return_value = pd.DataFrame(
        {
            "season": ["2023-24"],
            "league": ["EPL"],
            "goals": [1],
            "player": ["A"],
            "team": ["B"],
            "_player_lower": ["a"],
            "_team_lower": ["b"],
        }
    )
    with patch("soccer_server.tools.db.query_scalar", return_value=0):
        result = tool_data_status({})
    assert result["status"] == "OK"
    assert result.get("query_engine") == "duckdb"
    assert "analytics_views" in result


def test_registry_has_new_tools() -> None:
    for name in (
        "get_player_match_log",
        "get_player_form",
        "get_team_stats",
        "compare_teams",
        "search_matches",
        "get_player_shot_map",
    ):
        assert name in TOOLS
        assert callable(TOOLS[name]["fn"])


@patch("soccer_server.tools.get_unified")
def test_get_player_unchanged_with_mock(mock_get_unified: MagicMock) -> None:
    mock_get_unified.return_value = pd.DataFrame(
        {
            "player": ["Test Player"],
            "team": ["FC"],
            "league": ["EPL"],
            "season": ["2024-2025"],
            "pos": ["FW"],
            "goals": [10],
            "assists": [2],
            "xg": [8.0],
            "minutes": [2000],
            "games": [30],
            "_player_lower": ["test player"],
            "_team_lower": ["fc"],
        }
    )
    out = tool_get_player({"name": "Test", "full_stats": True})
    assert out["count"] == 1
    assert out["players"][0]["player"] == "Test Player"
