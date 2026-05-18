"""Shared helpers for MCP tools: filtering, ClubElo / SofaScore resolution, storage reads."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from collect_data.storage import get_backend, load_parquets


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
        col = (
            "minutes"
            if "minutes" in df.columns
            else ("ninety_s" if "ninety_s" in df.columns else None)
        )
        if col:
            df = df[df[col].fillna(0) >= min_minutes]
    return df


def _latest_clubelo_global_df() -> pd.DataFrame:
    be = get_backend()
    names = be.list_raw_glob("clubelo__global__*.parquet")
    if not names:
        return pd.DataFrame()
    last = sorted(names)[-1]
    try:
        return be.read_parquet_rel(f"raw/{last}")
    except Exception:
        return pd.DataFrame()


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
    """Find SofaScore match_id from team names using sofascore_match_team_stats parquets."""
    dfp = load_parquets("sofascore_match_team_stats__*.parquet")
    if dfp.empty or "match_id" not in dfp.columns:
        return None
    sub = dfp
    if "period" in sub.columns:
        sub = sub[sub["period"].astype(str).str.upper() == "ALL"]
    if league and "league" in sub.columns:
        sub = sub[sub["league"].astype(str).str.contains(league, case=False, na=False)]
    if season and "season" in sub.columns:
        sub = sub[sub["season"].astype(str) == str(season)]
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
