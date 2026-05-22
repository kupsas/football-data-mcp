"""Shared helpers for MCP tools: filtering, ClubElo / SofaScore resolution, DuckDB reads."""

from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

from collect_data.storage import get_backend
from soccer_server import db


def _parse_age(v: object) -> float:
    try:
        return float(str(v).split("-")[0])
    except Exception:
        return float("nan")


def _safe(v: Any) -> Any:
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


def _player_query_tokens(query: str) -> list[str]:
    """Split a player search into lowercase tokens (handles hyphens and spaces)."""
    return [t for t in re.split(r"[\s\-]+", query.lower()) if t]


def _player_name_matches_tokens(name_lower: str, tokens: list[str]) -> bool:
    """True when every query token appears somewhere in the stored player name."""
    if not tokens:
        return False
    return all(tok in name_lower for tok in tokens)


def suggest_similar_players(
    df: pd.DataFrame,
    query: str,
    n: int = 6,
) -> list[str]:
    """
    Return up to ``n`` player names similar to ``query``.

    Combines token overlap (fixes reversed names like Heung-Min Son vs Son Heung-Min)
    with rapidfuzz WRatio on unique names in ``df``.
    """
    if df.empty or not str(query).strip():
        return []
    if "player" not in df.columns:
        return []

    names = df["player"].dropna().astype(str).unique().tolist()
    if not names:
        return []

    tokens = _player_query_tokens(query)
    scores: dict[str, float] = {}

    for name in names:
        low = name.lower()
        if tokens and _player_name_matches_tokens(low, tokens):
            scores[name] = max(scores.get(name, 0.0), 95.0)
        elif tokens:
            overlap = sum(1 for t in tokens if t in low) / len(tokens)
            if overlap >= 0.5:
                scores[name] = max(scores.get(name, 0.0), 50.0 + 40.0 * overlap)

    try:
        from rapidfuzz import fuzz, process

        for name, score, _ in process.extract(
            query,
            names,
            scorer=fuzz.WRatio,
            limit=max(n * 3, n),
            score_cutoff=55,
        ):
            scores[name] = max(scores.get(name, 0.0), float(score))
    except ImportError:
        qlow = query.lower()
        for name in names:
            if qlow in name.lower() or name.lower() in qlow:
                scores[name] = max(scores.get(name, 0.0), 70.0)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ranked[:n]]


def _filter(
    df: pd.DataFrame,
    player: str | None = None,
    team: str | None = None,
    league: str | None = None,
    season: str | None = None,
    position: str | None = None,
    min_minutes: int | None = None,
    nation: str | None = None,
) -> pd.DataFrame:
    """Filter a unified DataFrame in memory (used after DuckDB load)."""
    if player:
        tokens = _player_query_tokens(player)
        if tokens:
            df = df[
                df["_player_lower"].apply(
                    lambda s: _player_name_matches_tokens(str(s), tokens)
                    if pd.notna(s)
                    else False
                )
            ]
        else:
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
        col = (
            "minutes"
            if "minutes" in df.columns
            else ("ninety_s" if "ninety_s" in df.columns else None)
        )
        if col:
            df = df[df[col].fillna(0) >= min_minutes]
    return df


def filter_unified_sql(
    *,
    player: str | None = None,
    team: str | None = None,
    league: str | None = None,
    season: str | None = None,
    position: str | None = None,
    min_minutes: int | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Load filtered rows from ``unified_prepared`` via DuckDB."""
    clauses: list[str] = ["1=1"]
    params: list[Any] = []
    if player:
        clauses.append("_player_lower LIKE ?")
        params.append(f"%{player.lower()}%")
    if team:
        clauses.append("_team_lower LIKE ?")
        params.append(f"%{team.lower()}%")
    if league:
        clauses.append("lower(league) LIKE ?")
        params.append(f"%{league.lower()}%")
    if season:
        clauses.append("season = ?")
        params.append(season)
    if position:
        clauses.append("lower(CAST(pos AS VARCHAR)) LIKE ?")
        params.append(f"%{position.lower()}%")
    if min_minutes:
        clauses.append("COALESCE(minutes_computed, 0) >= ?")
        params.append(float(min_minutes))
    sql = f"SELECT * FROM unified_prepared WHERE {' AND '.join(clauses)}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return db.query(sql, params)


def _latest_clubelo_global_df() -> pd.DataFrame:
    be = get_backend()
    names = be.list_raw_glob("clubelo__global__*.parquet")
    if not names:
        return pd.DataFrame()
    if db.table_empty("clubelo_global"):
        return pd.DataFrame()
    return db.query("SELECT * FROM clubelo_global")


def _clubelo_team_context(team_name: str | None) -> dict | None:
    """Match a club name from unified CSV to ClubElo global snapshot (latest file)."""
    if not team_name or not isinstance(team_name, str):
        return None
    df = _latest_clubelo_global_df()
    if df.empty or "club" not in df.columns:
        return None
    clubs = df["club"].astype(str).tolist()
    try:
        from rapidfuzz import fuzz, process

        hit = process.extractOne(
            team_name,
            clubs,
            scorer=fuzz.WRatio,
            score_cutoff=78,
        )
        if hit is None:
            return None
        club_name, score, _ = hit
        row = df.loc[df["club"].astype(str) == club_name].iloc[0]
        return {
            "clubelo_club": str(row["club"]),
            "clubelo_rank": int(row["rank"]) if pd.notna(row.get("rank")) else None,
            "clubelo_elo": float(row["elo"]) if pd.notna(row.get("elo")) else None,
            "clubelo_country": str(row.get("country", "")),
            "clubelo_match_score": int(score),
        }
    except ImportError:
        tlow = team_name.lower()
        for _, row in df.iterrows():
            c = str(row["club"]).lower()
            if c in tlow or tlow in c:
                return {
                    "clubelo_club": str(row["club"]),
                    "clubelo_rank": int(row["rank"]) if pd.notna(row.get("rank")) else None,
                    "clubelo_elo": float(row["elo"]) if pd.notna(row.get("elo")) else None,
                    "clubelo_country": str(row.get("country", "")),
                    "clubelo_match_score": None,
                }
        return None


def _resolve_sofascore_match_id(
    home: str,
    away: str,
    league: str | None = None,
    season: str | None = None,
) -> int | None:
    """Find SofaScore match_id from team names using DuckDB match_index / team stats."""
    if db.table_empty("sofascore_match_team_stats"):
        return None

    clauses = ["upper(CAST(period AS VARCHAR)) = 'ALL'"]
    params: list[Any] = []
    if league:
        clauses.append("lower(league) LIKE ?")
        params.append(f"%{league.lower()}%")
    if season:
        clauses.append("season = ?")
        params.append(season)
    sql = f"""
        SELECT DISTINCT match_id, home_team, away_team
        FROM sofascore_match_team_stats
        WHERE {' AND '.join(clauses)}
    """
    sub = db.query(sql, params)
    if sub.empty:
        return None

    try:
        from rapidfuzz import fuzz

        best_mid: int | None = None
        best = -1.0
        for mid in sub["match_id"].dropna().unique():
            chunk = sub[sub["match_id"] == mid].head(1)
            if chunk.empty:
                continue
            r = chunk.iloc[0]
            h, a = str(r.get("home_team", "")), str(r.get("away_team", ""))
            s = fuzz.WRatio(home.lower(), h.lower()) + fuzz.WRatio(away.lower(), a.lower())
            if s > best:
                best = s
                best_mid = int(mid)
        return best_mid if best >= 160 else None
    except ImportError:
        for mid in sub["match_id"].dropna().unique():
            chunk = sub[sub["match_id"] == mid].head(1)
            r = chunk.iloc[0]
            h, a = str(r.get("home_team", "")).lower(), str(r.get("away_team", "")).lower()
            if home.lower() in h and away.lower() in a:
                return int(mid)
        return None
