#!/usr/bin/env python3
"""
Soccer MCP Server — 7+1 tools covering all collected data.

Tools
-----
1. get_player          — find a player, return full stats + financial data
2. scout_position      — top players for a position (absorbs get_league_leaders via sort_by)
3. compare_players     — side-by-side stat + financial comparison
4. find_similar_players — cosine-similarity matching with optional budget cap
5. get_league_table    — Understat xG league table (overall / home / away)
6. get_match           — per-match shot data and player rosters
7. get_player_history  — form (per-match), market value history, or transfers
8. data_status         — coverage check (utility)
"""

import json
import sys
import logging
import math
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DATA_DIR    = Path(__file__).parent / "data"
RAW_DIR     = DATA_DIR / "raw"
UNIFIED_CSV = DATA_DIR / "unified_player_stats.csv"

# ── Data loading ──────────────────────────────────────────────────────────────

_df: pd.DataFrame | None = None


def _load_data() -> pd.DataFrame:
    global _df
    if _df is not None:
        return _df

    if not UNIFIED_CSV.exists():
        log.warning(f"Data file not found: {UNIFIED_CSV} — run collect_data.py first.")
        return pd.DataFrame()

    df = pd.read_csv(UNIFIED_CSV, low_memory=False)

    # Keep these as strings; everything else gets coerced to numeric
    id_cols = {
        "player", "nation", "pos", "team", "league", "season",
        "player_id", "team_id", "understat_id", "tm_id",
        "nationality", "citizenship", "tm_position",
        "contract_expiration", "dob",
    }
    for col in df.columns:
        if col not in id_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["_player_lower"] = df["player"].astype(str).str.lower()
    df["_team_lower"]   = df["team"].astype(str).str.lower()

    if "age" in df.columns:
        df["age_num"] = df["age"].apply(_parse_age)
    else:
        df["age_num"] = float("nan")

    if "minutes" not in df.columns and "ninety_s" in df.columns:
        df["minutes"] = df["ninety_s"] * 90

    _df = df
    log.info(
        f"Loaded {len(df)} rows × {len(df.columns)} cols | "
        f"{df['league'].nunique()} leagues | {df['season'].nunique()} seasons"
    )
    return df


def _parse_age(v) -> float:
    try:
        return float(str(v).split("-")[0])
    except Exception:
        return float("nan")


def _safe(v) -> Any:
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return None if math.isnan(f) else round(f, 3)
    if isinstance(v, np.bool_):
        return bool(v)
    return v


def _row_to_dict(row: pd.Series) -> dict:
    return {k: _safe(v) for k, v in row.items() if not k.startswith("_")}


def _filter(df: pd.DataFrame,
            player: str | None = None,
            team: str | None = None,
            league: str | None = None,
            season: str | None = None,
            position: str | None = None,
            min_minutes: int | None = None,
            nation: str | None = None) -> pd.DataFrame:
    if player:
        df = df[df["_player_lower"].str.contains(player.lower(), na=False)]
    if team:
        df = df[df["_team_lower"].str.contains(team.lower(), na=False)]
    if league:
        df = df[df["league"].str.contains(league, case=False, na=False)]
    if season:
        df = df[df["season"] == season]
    if position and "pos" in df.columns:
        df = df[df["pos"].astype(str).str.contains(position, case=False, na=False)]
    if nation and "nation" in df.columns:
        df = df[df["nation"].astype(str).str.contains(nation, case=False, na=False)]
    if min_minutes:
        col = "minutes" if "minutes" in df.columns else (
              "ninety_s" if "ninety_s" in df.columns else None)
        if col:
            df = df[df[col].fillna(0) >= min_minutes]
    return df


def _load_parquets(pattern: str) -> pd.DataFrame:
    frames = []
    for f in sorted(RAW_DIR.glob(pattern)):
        try:
            frames.append(pd.read_parquet(f))
        except Exception as e:
            log.warning(f"Could not load {f.name}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ── Tool implementations ──────────────────────────────────────────────────────

def tool_get_player(args: dict) -> dict:
    df = _load_data()
    if df.empty:
        return {"error": "No data loaded — run collect_data.py first."}

    name       = args.get("name", "")
    season     = args.get("season")
    league     = args.get("league")
    team       = args.get("team")
    full_stats = args.get("full_stats", True)

    results = _filter(df, player=name, season=season, league=league, team=team)
    if results.empty:
        return {"error": f"No player found matching '{name}'"}

    results = results.sort_values("season", ascending=False)

    summary_cols = [c for c in [
        "player", "team", "league", "season", "pos", "age", "nation",
        "goals", "assists", "xg", "xag", "npxg", "minutes", "games",
        "sofascore_rating", "market_value_eur", "contract_expiration",
    ] if c in results.columns]

    if len(results) > 5 and not full_stats:
        return {
            "count": len(results),
            "note": "Multiple results. Narrow with season/league/team or set full_stats=true.",
            "players": [_row_to_dict(r) for _, r in results[summary_cols].head(10).iterrows()],
        }

    players = []
    for _, row in results.head(5).iterrows():
        players.append(_row_to_dict(row) if full_stats else _row_to_dict(row[summary_cols]))

    return {
        "count": len(results),
        "players": players,
        "note": "Showing up to 5 rows. Specify season/league/team to filter." if len(results) > 1 else None,
    }


def tool_scout_position(args: dict) -> dict:
    df = _load_data()
    if df.empty:
        return {"error": "No data loaded."}

    position      = args.get("position", "FW")
    league        = args.get("league")
    season        = args.get("season")
    sort_by       = args.get("sort_by")
    min_minutes   = int(args.get("min_minutes", 900))
    max_age       = args.get("max_age")
    max_value_eur = args.get("max_market_value_eur")
    limit         = int(args.get("limit", 15))

    results = _filter(df, position=position, league=league, season=season,
                      min_minutes=min_minutes)

    if max_age and "age_num" in results.columns:
        results = results[results["age_num"].fillna(99) <= float(max_age)]
    if max_value_eur and "market_value_eur" in results.columns:
        results = results[results["market_value_eur"].fillna(float("inf")) <= float(max_value_eur)]

    if results.empty:
        return {"error": f"No players found for position '{position}' with given filters."}

    if not sort_by:
        pos_up = position.upper()
        if "GK" in pos_up:
            sort_by = next((c for c in ["save_pct", "goals_against_per90", "clean_sheet_pct"]
                            if c in results.columns), None)
        elif "CB" in pos_up or ("DF" in pos_up and "FW" not in pos_up and "MF" not in pos_up):
            sort_by = next((c for c in ["tackles", "interceptions", "aerials_won_pct"]
                            if c in results.columns), None)
        elif any(p in pos_up for p in ("MF", "DM", "CM")):
            sort_by = next((c for c in ["progressive_passes", "key_passes", "xag"]
                            if c in results.columns), None)
        else:
            sort_by = next((c for c in ["goals", "xg", "xag"] if c in results.columns), None)

    if sort_by and sort_by not in results.columns:
        candidates = [c for c in results.columns if sort_by.lower() in c.lower()
                      and results[c].dtype in (float, int, "float64", "int64")]
        sort_by = candidates[0] if candidates else None

    results = results.nlargest(limit, sort_by) if sort_by else results.head(limit)

    base = ["player", "team", "league", "season", "pos", "age", "minutes"]
    extra = [sort_by] if sort_by else []
    extra += ["goals", "assists", "xg", "xag", "npxg",
              "sofascore_rating", "market_value_eur", "contract_expiration"]
    seen: set = set()
    show_cols = []
    for c in base + extra:
        if c and c in results.columns and c not in seen:
            show_cols.append(c)
            seen.add(c)

    return {
        "position": position,
        "sort_by":  sort_by or "default",
        "league":   league or "All",
        "season":   season or "All",
        "count":    len(results),
        "players":  [_row_to_dict(r) for _, r in results[show_cols].iterrows()],
    }


def tool_compare_players(args: dict) -> dict:
    df = _load_data()
    if df.empty:
        return {"error": "No data loaded."}

    names  = args.get("names", [])
    season = args.get("season")
    stats  = args.get("stats") or [
        "goals", "assists", "xg", "xag", "npxg", "xg_overperformance",
        "shots", "key_passes", "pass_completion_pct", "progressive_passes",
        "progressive_carries", "tackles_won", "interceptions",
        "sca", "gca", "aerials_won_pct", "sofascore_rating",
        "minutes", "games", "market_value_eur", "contract_expiration",
    ]

    comparisons = []
    for name in names:
        results = _filter(df, player=name, season=season)
        if results.empty:
            comparisons.append({"player": name, "error": "Not found"})
            continue
        row = results.sort_values("season", ascending=False).iloc[0]
        d = {
            "player": row.get("player", name),
            "team":   row.get("team"),
            "league": row.get("league"),
            "season": row.get("season"),
        }
        for stat in stats:
            if stat in row.index:
                d[stat] = _safe(row[stat])
        comparisons.append(d)

    return {"comparisons": comparisons}


def tool_find_similar_players(args: dict) -> dict:
    df = _load_data()
    if df.empty:
        return {"error": "No data loaded."}

    name          = args.get("name", "")
    season        = args.get("season")
    n_results     = int(args.get("n", 10))
    min_minutes   = int(args.get("min_minutes", 900))
    max_value_eur = args.get("max_market_value_eur")
    same_league   = bool(args.get("same_league", False))

    target_rows = _filter(df, player=name, season=season)
    if target_rows.empty:
        return {"error": f"Player '{name}' not found"}
    target = target_rows.sort_values("season", ascending=False).iloc[0]

    preferred = [
        "xg", "npxg", "xag", "xg_chain", "xg_buildup", "shots",
        "pass_completion_pct", "progressive_passes", "progressive_carries",
        "tackles_won", "interceptions", "sca", "gca",
        "aerials_won_pct", "key_passes", "touches",
    ]
    exclude = {
        "player", "team", "league", "season", "pos", "age", "nation",
        "player_id", "team_id", "understat_id", "tm_id", "born",
        "age_num", "minutes", "games", "starts", "ninety_s",
        "market_value_eur", "goals", "assists",
    }
    stat_cols = [
        c for c in df.columns
        if c not in exclude
        and not c.startswith("_")
        and df[c].dtype in (float, int, "float64", "int64")
        and df[c].max() > 0
    ]
    stat_cols_use = [c for c in preferred if c in stat_cols]
    stat_cols_use += [c for c in stat_cols if c not in stat_cols_use]
    stat_cols_use = stat_cols_use[:15]

    if not stat_cols_use:
        return {"error": "No numeric stat columns found for comparison."}

    pool = _filter(df, season=season, min_minutes=min_minutes)
    if same_league and "league" in target.index:
        pool = pool[pool["league"] == target["league"]]
    pool = pool[~pool["_player_lower"].str.contains(name.lower(), na=False)]
    if max_value_eur and "market_value_eur" in pool.columns:
        pool = pool[pool["market_value_eur"].fillna(float("inf")) <= float(max_value_eur)]

    target_vals = np.array([_safe(target.get(c, 0)) or 0 for c in stat_cols_use], dtype=float)
    pool_vals   = pool[stat_cols_use].fillna(0).values.astype(float)

    stds = pool_vals.std(axis=0)
    stds[stds == 0] = 1
    target_norm = target_vals / stds
    pool_norm   = pool_vals / stds

    a_norm = np.linalg.norm(target_norm)
    if a_norm == 0:
        return {"error": "Target player has no stat data for similarity calculation."}
    b_norms = np.linalg.norm(pool_norm, axis=1)
    b_norms[b_norms == 0] = 1
    sims = (pool_norm @ target_norm) / (b_norms * a_norm)

    pool = pool.copy()
    pool["_similarity"] = sims
    top = pool.nlargest(n_results, "_similarity")

    cols = [c for c in [
        "player", "team", "league", "season", "pos",
        "goals", "assists", "xg", "minutes",
        "market_value_eur", "_similarity",
    ] if c in top.columns]

    return {
        "target": f"{target.get('player')} ({target.get('team')}, {target.get('season')})",
        "similar_players": [_row_to_dict(r) for _, r in top[cols].iterrows()],
    }


def tool_get_league_table(args: dict) -> dict:
    league = args.get("league", "")
    season = args.get("season", "2024-2025")
    split  = args.get("split", "overall")

    if split not in ("overall", "home", "away"):
        split = "overall"

    slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
    path = RAW_DIR / f"understat_league_table__{slug}__{split}.parquet"

    if not path.exists():
        available = sorted(p.name for p in RAW_DIR.glob("understat_league_table__*.parquet"))
        return {
            "error": (
                f"League table not found for {league!r} {season} ({split}). "
                "Run: python3 collect_data.py --understat-tables-only"
            ),
            "available_files": available[:20],
        }

    df = pd.read_parquet(path)
    return {
        "league": league,
        "season": season,
        "split":  split,
        "table":  [_row_to_dict(r) for _, r in df.iterrows()],
    }


def tool_get_match(args: dict) -> dict:
    match_id = args.get("match_id")
    include  = args.get("include", "both")

    if not match_id:
        return {"error": "match_id is required."}

    mid    = int(match_id)
    result: dict = {"match_id": mid}

    # Look up match metadata
    for f in sorted(RAW_DIR.glob("understat_match_info__*.parquet")):
        try:
            info_df = pd.read_parquet(f)
            row = info_df[info_df["match_id"] == mid]
            if not row.empty:
                result["match_info"] = _row_to_dict(row.iloc[0])
                break
        except Exception:
            pass

    if include in ("shots", "both"):
        shots_frames = []
        for f in sorted(RAW_DIR.glob("understat_match_shots__*.parquet")):
            try:
                df = pd.read_parquet(f)
                mask = df["match_id"] == mid
                if mask.any():
                    shots_frames.append(df[mask])
            except Exception:
                pass
        if shots_frames:
            shots_df = pd.concat(shots_frames, ignore_index=True)
            result["shots"]       = [_row_to_dict(r) for _, r in shots_df.iterrows()]
            result["shots_count"] = len(shots_df)
        else:
            result["shots"] = []

    if include in ("rosters", "both"):
        roster_frames = []
        for f in sorted(RAW_DIR.glob("understat_rosters__*.parquet")):
            try:
                df = pd.read_parquet(f)
                mask = df["match_id"] == mid
                if mask.any():
                    roster_frames.append(df[mask])
            except Exception:
                pass
        if roster_frames:
            rosters_df = pd.concat(roster_frames, ignore_index=True)
            result["rosters"]      = [_row_to_dict(r) for _, r in rosters_df.iterrows()]
            result["roster_count"] = len(rosters_df)
        else:
            result["rosters"] = []

    if not result.get("match_info") and not result.get("shots") and not result.get("rosters"):
        return {
            "error": (
                f"No data found for match_id {mid}. "
                "Run: python3 collect_data.py --understat-matches-only"
            )
        }

    return result


def tool_get_player_history(args: dict) -> dict:
    name         = args.get("name", "")
    history_type = args.get("type", "form")
    season       = args.get("season")
    league       = args.get("league")
    limit        = int(args.get("limit", 50))

    if history_type not in ("form", "value", "transfers"):
        return {"error": "type must be 'form', 'value', or 'transfers'"}

    if history_type == "form":
        rosters = _load_parquets("understat_rosters__*.parquet")
        if rosters.empty:
            return {
                "error": "No roster data. Run: python3 collect_data.py --understat-matches-only"
            }

        mask = rosters["player"].astype(str).str.lower().str.contains(name.lower(), na=False)
        player_rows = rosters[mask]
        if season:
            player_rows = player_rows[player_rows["season"] == season]
        if league:
            player_rows = player_rows[
                player_rows["league"].str.contains(league, case=False, na=False)
            ]

        if player_rows.empty:
            return {"error": f"No match-level data found for '{name}'"}

        match_info = _load_parquets("understat_match_info__*.parquet")
        if not match_info.empty:
            player_rows = player_rows.merge(
                match_info[["match_id", "home_team", "away_team",
                            "datetime", "home_goals", "away_goals"]],
                on="match_id", how="left",
            )

        return {
            "player":  name,
            "type":    "form",
            "records": len(player_rows),
            "matches": [_row_to_dict(r) for _, r in player_rows.head(limit).iterrows()],
        }

    # Resolve tm_id from unified CSV for value / transfers
    tm_id = None
    df = _load_data()
    if not df.empty and "tm_id" in df.columns:
        candidates = _filter(df, player=name, season=season, league=league)
        if not candidates.empty:
            val = candidates.sort_values("season", ascending=False).iloc[0].get("tm_id")
            if pd.notna(val) and str(val).strip():
                tm_id = str(val).strip()

    if history_type == "value":
        mv_df = _load_parquets("transfermarkt_mv_history__*.parquet")
        if mv_df.empty:
            return {
                "error": "No market value history. Run: python3 collect_data.py --transfermarkt-only"
            }

        if tm_id:
            rows = mv_df[mv_df["tm_id"].astype(str) == tm_id]
        else:
            tm_flat = _load_parquets("transfermarkt__*.parquet")
            if not tm_flat.empty:
                match = tm_flat[
                    tm_flat["tm_name"].astype(str).str.lower().str.contains(name.lower(), na=False)
                ]
                if not match.empty:
                    tm_id = str(match.iloc[0]["tm_id"])
                    rows = mv_df[mv_df["tm_id"].astype(str) == tm_id]
                else:
                    rows = pd.DataFrame()
            else:
                rows = pd.DataFrame()

        if rows.empty:
            return {"error": f"No market value history for '{name}' (tm_id={tm_id})"}

        return {
            "player":  name,
            "tm_id":   tm_id,
            "type":    "value",
            "history": [_row_to_dict(r) for _, r in rows.sort_values("date").head(limit).iterrows()],
        }

    # history_type == "transfers"
    tr_df = _load_parquets("transfermarkt_transfers__*.parquet")
    if tr_df.empty:
        return {
            "error": "No transfer history. Run: python3 collect_data.py --transfermarkt-only"
        }

    if tm_id:
        rows = tr_df[tr_df["tm_id"].astype(str) == tm_id]
    else:
        rows = tr_df[
            tr_df["tm_name"].astype(str).str.lower().str.contains(name.lower(), na=False)
        ]

    if rows.empty:
        return {"error": f"No transfer history for '{name}'"}

    return {
        "player":    name,
        "tm_id":     tm_id,
        "type":      "transfers",
        "transfers": [_row_to_dict(r) for _, r in rows.drop_duplicates().head(limit).iterrows()],
    }


def tool_data_status(args: dict) -> dict:
    df = _load_data()

    raw_counts = {}
    if RAW_DIR.exists():
        raw_counts = {
            "understat_league_tables": len(list(RAW_DIR.glob("understat_league_table__*.parquet"))),
            "understat_match_info":    len(list(RAW_DIR.glob("understat_match_info__*.parquet"))),
            "understat_match_shots":   len(list(RAW_DIR.glob("understat_match_shots__*.parquet"))),
            "understat_rosters":       len(list(RAW_DIR.glob("understat_rosters__*.parquet"))),
            "transfermarkt_profiles":  len(list(RAW_DIR.glob("transfermarkt__*.parquet"))),
            "transfermarkt_mv_history":len(list(RAW_DIR.glob("transfermarkt_mv_history__*.parquet"))),
            "transfermarkt_transfers": len(list(RAW_DIR.glob("transfermarkt_transfers__*.parquet"))),
            "capology_wages":          len(list(RAW_DIR.glob("capology__*.parquet"))),
        }

    if df.empty:
        return {
            "status":    "NO DATA",
            "message":   "Run: python3 collect_data.py",
            "raw_files": raw_counts,
        }

    status: dict = {
        "status":    "OK",
        "rows":      len(df),
        "columns":   len(df.columns),
        "leagues":   sorted(df["league"].unique().tolist()) if "league" in df.columns else [],
        "seasons":   sorted(df["season"].unique().tolist()) if "season" in df.columns else [],
        "raw_files": raw_counts,
    }

    key_stats = [
        "goals", "xg", "npxg", "xag", "tackles_won", "interceptions",
        "aerials_won_pct", "pass_completion_pct", "sofascore_rating",
        "market_value_eur", "contract_expiration",
    ]
    coverage: dict = {}
    if "season" in df.columns:
        for season in sorted(df["season"].unique()):
            s = df[df["season"] == season]
            cov: dict = {"players": len(s)}
            for stat in key_stats:
                if stat in s.columns:
                    if s[stat].dtype == object:
                        pct = int(100 * s[stat].notna().sum() / len(s))
                    else:
                        pct = int(100 * (s[stat].fillna(0) > 0).sum() / len(s))
                    cov[stat] = f"{pct}%"
            coverage[season] = cov
    status["coverage"] = coverage

    return status


# ── Tool registry ─────────────────────────────────────────────────────────────

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
                "name":       {"type": "string",  "description": "Player name or partial match"},
                "season":     {"type": "string",  "description": "e.g. '2024-2025'"},
                "league":     {"type": "string",  "description": "e.g. 'England Premier League'"},
                "team":       {"type": "string",  "description": "Club name or partial"},
                "full_stats": {"type": "boolean", "description": "Return all columns (default true)"},
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
                "position":             {"type": "string",  "description": "e.g. 'FW', 'MF', 'DF', 'GK', 'CB', 'CM'"},
                "league":               {"type": "string"},
                "season":               {"type": "string"},
                "sort_by":              {"type": "string",  "description": "Stat column to rank by (overrides position default)"},
                "min_minutes":          {"type": "integer", "description": "Minimum minutes played (default 900)"},
                "max_age":              {"type": "number",  "description": "Maximum player age"},
                "max_market_value_eur": {"type": "number",  "description": "Budget cap in EUR (e.g. 20000000 for €20m)"},
                "limit":                {"type": "integer", "description": "Max results (default 15)"},
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
                "names":  {"type": "array",  "items": {"type": "string"}, "description": "List of player names"},
                "season": {"type": "string"},
                "stats":  {"type": "array",  "items": {"type": "string"}, "description": "Stat column names to include (optional, defaults to key stats)"},
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
                "name":                 {"type": "string",  "description": "Target player name"},
                "season":               {"type": "string"},
                "n":                    {"type": "integer", "description": "Number of results (default 10)"},
                "min_minutes":          {"type": "integer", "description": "Minimum minutes for candidates (default 900)"},
                "max_market_value_eur": {"type": "number",  "description": "Budget cap in EUR"},
                "same_league":          {"type": "boolean", "description": "Restrict candidates to the same league (default false)"},
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
                "league": {"type": "string",  "description": "Big 5 league name"},
                "season": {"type": "string",  "description": "e.g. '2024-2025' (default)"},
                "split":  {"type": "string",  "description": "'overall', 'home', or 'away' (default 'overall')"},
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
                "include":  {"type": "string",  "description": "'shots', 'rosters', or 'both' (default 'both')"},
            },
            "required": ["match_id"],
        },
        "fn": tool_get_match,
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
                "name":   {"type": "string",  "description": "Player name"},
                "type":   {"type": "string",  "description": "'form', 'value', or 'transfers' (default 'form')"},
                "season": {"type": "string",  "description": "Filter to a season (for form only)"},
                "league": {"type": "string",  "description": "Filter to a league (for form only)"},
                "limit":  {"type": "integer", "description": "Max records to return (default 50)"},
            },
            "required": ["name"],
        },
        "fn": tool_get_player_history,
    },
    "data_status": {
        "description": (
            "Check what data is available — leagues, seasons, per-season coverage percentages "
            "for key stats, and counts of all supplementary parquet files "
            "(match shots, rosters, market value history, wages)."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "fn": tool_data_status,
    },
}


# ── MCP protocol ──────────────────────────────────────────────────────────────

def _respond(req_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def _error(req_id, code: int, message: str):
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": req_id,
                    "error": {"code": code, "message": message}}) + "\n"
    )
    sys.stdout.flush()


def handle_request(req: dict):
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        _respond(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "soccer-data", "version": "3.0"},
        })

    elif method == "tools/list":
        _respond(req_id, {"tools": [
            {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
            for name, meta in TOOLS.items()
        ]})

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name not in TOOLS:
            _error(req_id, -32601, f"Unknown tool: {tool_name}")
            return

        try:
            result = TOOLS[tool_name]["fn"](tool_args)
            _respond(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}]
            })
        except Exception as e:
            log.exception(f"Error running tool {tool_name}")
            _error(req_id, -32603, str(e))

    elif method == "notifications/initialized":
        pass

    else:
        if req_id is not None:
            _error(req_id, -32601, f"Method not found: {method}")


def main():
    _load_data()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handle_request(req)
        except json.JSONDecodeError as e:
            log.error(f"JSON decode error: {e}")


if __name__ == "__main__":
    main()
