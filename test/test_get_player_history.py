"""Tests for get_player_history (Understat form only; no TM career history)."""

from __future__ import annotations

from soccer_server.tools import tool_get_player_history


def test_get_player_history_rejects_tm_history_types() -> None:
    for history_type in ("value", "transfers"):
        out = tool_get_player_history({"name": "Test Player", "type": history_type})
        assert out.get("error")
        assert "form" in out["error"].lower()
        assert "get_player" in out["error"].lower()
