"""
Soccer MCP Server — tools covering collected data + SofaScore match-level + ClubElo.

Run with: ``python -m soccer_server`` (stdio JSON-RPC for MCP clients).

Public API for hosted wrappers::

    from soccer_server.registry import TOOLS
    from soccer_server.cache import DataCache, get_unified
"""

from soccer_server.cache import DataCache, get_unified
from soccer_server.registry import TOOLS
from soccer_server.tools import (
    tool_compare_players,
    tool_data_status,
    tool_find_similar_players,
    tool_get_club_elo,
    tool_get_league_table,
    tool_get_match,
    tool_get_player,
    tool_get_player_history,
    tool_get_sofascore_match,
    tool_scout_position,
)

__all__ = [
    "TOOLS",
    "DataCache",
    "get_unified",
    "tool_get_player",
    "tool_scout_position",
    "tool_compare_players",
    "tool_find_similar_players",
    "tool_get_league_table",
    "tool_get_match",
    "tool_get_sofascore_match",
    "tool_get_club_elo",
    "tool_get_player_history",
    "tool_data_status",
]
