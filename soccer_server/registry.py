"""MCP tool registry: name → description, JSON Schema, callable."""

from __future__ import annotations

from soccer_server.tools import (
    tool_compare_players,
    tool_compare_teams,
    tool_data_status,
    tool_find_similar_players,
    tool_get_club_elo,
    tool_get_league_table,
    tool_get_match,
    tool_get_player,
    tool_get_player_form,
    tool_get_player_history,
    tool_get_player_match_log,
    tool_get_player_shot_map,
    tool_get_sofascore_match,
    tool_get_team_stats,
    tool_scout_position,
    tool_search_matches,
)

TOOLS = {
    "get_player": {
        "description": (
            "Find a player and return their season stats including xG, Sofascore rating, "
            "market value, and contract expiration. Use season/league/team to narrow results. "
            "Set full_stats=false for a compact summary when searching across many rows."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Player name or partial match"},
                "season": {"type": "string", "description": "e.g. '2024-2025'"},
                "league": {"type": "string", "description": "e.g. 'England Premier League'"},
                "team": {"type": "string", "description": "Club name or partial"},
                "full_stats": {
                    "type": "boolean",
                    "description": "Return all columns (default true)",
                },
            },
            "required": ["name"],
        },
        "fn": tool_get_player,
    },
    "scout_position": {
        "description": (
            "Find top players for a position, optionally sorted by any stat (sort_by), "
            "with budget and age filters. When sort_by is set this effectively acts as "
            "a league-leaders query (e.g. position='FW', sort_by='xg' gives top xG scorers)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "position": {"type": "string", "description": "e.g. 'FW', 'MF', 'DF', 'GK', 'CB', 'CM'"},
                "league": {"type": "string"},
                "season": {"type": "string"},
                "sort_by": {
                    "type": "string",
                    "description": "Stat column to rank by (overrides position default)",
                },
                "min_minutes": {"type": "integer", "description": "Minimum minutes played (default 900)"},
                "max_age": {"type": "number", "description": "Maximum player age"},
                "max_market_value_eur": {
                    "type": "number",
                    "description": "Budget cap in EUR (e.g. 20000000 for €20m)",
                },
                "limit": {"type": "integer", "description": "Max results (default 15)"},
            },
            "required": ["position"],
        },
        "fn": tool_scout_position,
    },
    "compare_players": {
        "description": (
            "Compare two or more players side-by-side across stats and financial data "
            "(market value, contract expiration, wages). Returns the most recent season "
            "for each player unless season is specified."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of player names",
                },
                "season": {"type": "string"},
                "stats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Stat column names to include (optional, defaults to key stats)",
                },
            },
            "required": ["names"],
        },
        "fn": tool_compare_players,
    },
    "find_similar_players": {
        "description": (
            "Find players with a similar statistical profile to a target player using "
            "cosine similarity. Supports budget cap and same-league restrictions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Target player name"},
                "season": {"type": "string"},
                "n": {"type": "integer", "description": "Number of results (default 10)"},
                "min_minutes": {"type": "integer", "description": "Minimum minutes for candidates (default 900)"},
                "max_market_value_eur": {"type": "number", "description": "Budget cap in EUR"},
                "same_league": {
                    "type": "boolean",
                    "description": "Restrict candidates to the same league (default false)",
                },
            },
            "required": ["name"],
        },
        "fn": tool_find_similar_players,
    },
    "get_league_table": {
        "description": (
            "Return an xG-enriched league table from Understat. Available for Big 5 leagues only "
            "(England Premier League, Spain La Liga, Germany Bundesliga, Italy Serie A, France Ligue 1). "
            "Columns include M, wins, draws, losses, pts, goals, goals_against, xG, xGA, npxG, "
            "npxGA, npxGD, xpts, PPDA, OPPDA, deep, deep_allowed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "league": {"type": "string", "description": "Big 5 league name"},
                "season": {"type": "string", "description": "e.g. '2024-2025' (default)"},
                "split": {
                    "type": "string",
                    "description": "'overall', 'home', or 'away' (default 'overall')",
                },
            },
            "required": ["league"],
        },
        "fn": tool_get_league_table,
    },
    "get_match": {
        "description": (
            "Get shot-level data and/or player rosters for a specific match identified by "
            "its Understat match_id integer. "
            "Shot fields: player, minute, result, X, Y, xG, situation, shot_type. "
            "Roster fields: player, position, minutes, goals, xG, assists, xA, xGChain, xGBuildup."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "match_id": {"type": "integer", "description": "Understat match ID"},
                "include": {
                    "type": "string",
                    "description": "'shots', 'rosters', or 'both' (default 'both')",
                },
            },
            "required": ["match_id"],
        },
        "fn": tool_get_match,
    },
    "get_sofascore_match": {
        "description": (
            "SofaScore match-level data (requires collect_data --sofascore-matches-only or full run). "
            "Use SofaScore match_id, or home_team + away_team with optional league/season to resolve id. "
            "include: 'all' (default), 'shots', 'team_stats', 'player_stats', or 'momentum'. "
            "period: 'ALL', '1ST', or '2ND' for team_stats rows only (default ALL)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "match_id": {"type": "integer", "description": "SofaScore event id"},
                "home_team": {
                    "type": "string",
                    "description": "Home club name (with away_team if no match_id)",
                },
                "away_team": {"type": "string", "description": "Away club name"},
                "league": {"type": "string", "description": "Optional filter when resolving by team names"},
                "season": {"type": "string", "description": "e.g. 2024-2025"},
                "include": {
                    "type": "string",
                    "description": "all | shots | team_stats | player_stats | momentum",
                },
                "period": {"type": "string", "description": "ALL | 1ST | 2ND for team_stats slice"},
                "limit": {"type": "integer", "description": "Max rows for shots/player_stats (default 500)"},
            },
            "required": [],
        },
        "fn": tool_get_sofascore_match,
    },
    "get_club_elo": {
        "description": (
            "Look up a club in the latest ClubElo global ratings (strength score + world rank) and "
            "show a sample of upcoming fixtures with derived win/draw/loss probabilities from ClubElo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Club name (fuzzy-matched to ClubElo club names)"},
            },
            "required": ["team"],
        },
        "fn": tool_get_club_elo,
    },
    "get_player_history": {
        "description": (
            "Historical records for a player from Understat or Transfermarkt. "
            "type='form': per-match xG/goals/assists/minutes from Understat (Big 5 only). "
            "type='value': market value timeline from Transfermarkt. "
            "type='transfers': full transfer history from Transfermarkt."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Player name"},
                "type": {
                    "type": "string",
                    "description": "'form', 'value', or 'transfers' (default 'form')",
                },
                "season": {"type": "string", "description": "Filter to a season (for form only)"},
                "league": {"type": "string", "description": "Filter to a league (for form only)"},
                "limit": {"type": "integer", "description": "Max records to return (default 50)"},
            },
            "required": ["name"],
        },
        "fn": tool_get_player_history,
    },
    "data_status": {
        "description": (
            "Check what data is available — leagues, seasons, per-season coverage percentages "
            "for key stats, counts of supplementary parquet files, manifest build timestamps "
            "(last_built_at, oldest_source_fetched_at), raw .freshness.json age, and DuckDB "
            "analytics view row counts."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "fn": tool_data_status,
    },
    "get_player_match_log": {
        "description": (
            "Per-match SofaScore stats for a player: rating, xG, xGoT, xA, goals, assists, "
            "shots, passes, opponent, home/away. Requires SofaScore match collection."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Player name (partial match)"},
                "season": {"type": "string"},
                "league": {"type": "string"},
                "team": {"type": "string"},
                "limit": {"type": "integer", "description": "Max matches (default 20)"},
                "home_only": {"type": "boolean"},
                "away_only": {"type": "boolean"},
            },
            "required": ["name"],
        },
        "fn": tool_get_player_match_log,
    },
    "get_player_form": {
        "description": (
            "Aggregated form from match-level ratings: avg rating, consistency (std dev), "
            "home/away splits, matches rated 7+, plus last 5 match ratings when available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "season": {"type": "string"},
                "league": {"type": "string"},
                "team": {"type": "string"},
                "limit": {"type": "integer", "description": "Max season profiles (default 10)"},
            },
            "required": ["name"],
        },
        "fn": tool_get_player_form,
    },
    "get_team_stats": {
        "description": (
            "Team season aggregates from SofaScore matches: avg xG for/against, possession, "
            "shots, big chances, home/away xG splits. Optional ClubElo context."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Club name (partial match)"},
                "season": {"type": "string"},
                "league": {"type": "string"},
                "limit": {"type": "integer", "description": "Max rows (default 5)"},
            },
            "required": ["team"],
        },
        "fn": tool_get_team_stats,
    },
    "compare_teams": {
        "description": (
            "Compare two or more teams side-by-side on season aggregates (xG, possession, shots)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Team names (at least 2)",
                },
                "season": {"type": "string"},
                "league": {"type": "string"},
            },
            "required": ["names"],
        },
        "fn": tool_compare_teams,
    },
    "search_matches": {
        "description": (
            "Search SofaScore matches by team (home or away), league, season. "
            "sort_by: xg_total (default), shots, or possession."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "team": {"type": "string", "description": "Filter matches involving this team"},
                "league": {"type": "string"},
                "season": {"type": "string"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
                "sort_by": {
                    "type": "string",
                    "description": "xg_total | shots | possession",
                },
            },
            "required": [],
        },
        "fn": tool_search_matches,
    },
    "get_player_shot_map": {
        "description": (
            "Shot-level data for a player: coordinates, xG, body part, situation, plus "
            "season shot profile aggregates when available."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "season": {"type": "string"},
                "league": {"type": "string"},
                "limit": {"type": "integer", "description": "Max shot rows (default 100)"},
            },
            "required": ["name"],
        },
        "fn": tool_get_player_shot_map,
    },
}
