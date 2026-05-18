#!/usr/bin/env python3
"""
Soccer MCP Server — tools covering collected data + SofaScore match-level + ClubElo.

Tools
-----
1. get_player              — find a player, return full stats + optional ClubElo team context
2. scout_position          — top players for a position
3. compare_players         — side-by-side stat comparison
4. find_similar_players    — cosine-similarity matching
5. get_league_table        — Understat xG league table
6. get_match               — Understat match_id: shots + rosters
7. get_sofascore_match     — SofaScore match_id: shots, team stats, player stats, momentum
8. get_club_elo            — Club strength (Elo) + upcoming fixtures with win probabilities
9. get_player_history      — form / value / transfers
10. data_status            — coverage + raw file counts + freshness
"""

import json
import sys
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

DATA_DIR    = Path(__file__).parent / "data"
RAW_DIR     = DATA_DIR / "raw"
UNIFIED_PARQUET = DATA_DIR / "unified_player_stats.parquet"
UNIFIED_CSV = DATA_DIR / "unified_player_stats.csv"
FRESHNESS_PATH = RAW_DIR / ".freshness.json"
MANIFEST_PATH = DATA_DIR / "manifest.json"

# ── Data loading ──────────────────────────────────────────────────────────────

_df: pd.DataFrame | None = None


def _load_data() -> pd.DataFrame:
    global _df
    if _df is not None:
        return _df

    if UNIFIED_PARQUET.exists():
        df = pd.read_parquet(UNIFIED_PARQUET)
    elif UNIFIED_CSV.exists():
        df = pd.read_csv(UNIFIED_CSV, low_memory=False)
    else:
        log.warning(
            f"No unified table found ({UNIFIED_PARQUET.name} or {UNIFIED_CSV.name}) — "
            "run ``python -m collect_data`` first."
        )
        return pd.DataFrame()

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


def _freshness_summary() -> dict:
    """Summarise data/raw/.freshness.json for MCP data_status."""
    out: dict = {"freshness_entries": 0}
    if not FRESHNESS_PATH.exists():
        return out
    try:
        data = json.loads(FRESHNESS_PATH.read_text(encoding="utf-8"))
        out["freshness_entries"] = len(data)
        times = []
        for meta in data.values():
            ts = meta.get("fetched_at")
            if not ts:
                continue
            try:
                times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except ValueError:
                continue
        if times:
            oldest = min(times)
            newest = max(times)
            out["oldest_raw_fetch_utc"] = oldest.isoformat()
            out["newest_raw_fetch_utc"] = newest.isoformat()
            age_days = (datetime.now(timezone.utc) - newest).total_seconds() / 86400
            out["newest_raw_fetch_age_days"] = round(age_days, 2)
    except Exception as e:
        out["freshness_error"] = str(e)
    return out


def _manifest_summary() -> dict:
    """Read data/manifest.json for build timestamps (written by collect_data._finalize_and_save)."""
    if not MANIFEST_PATH.exists():
        return {}
    try:
        m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        return {
            k: m[k]
            for k in ("last_built_at", "oldest_source_fetched_at")
            if k in m and m[k] is not None
        }
    except Exception as e:
        return {"manifest_error": str(e)}


def _latest_clubelo_global_df() -> pd.DataFrame:
    paths = sorted(RAW_DIR.glob("clubelo__global__*.parquet"))
    if not paths:
        return pd.DataFrame()
    try:
        return pd.read_parquet(paths[-1])
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
        from rapidfuzz import process, fuzz

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
            "clubelo_club":       str(row["club"]),
            "clubelo_rank":       int(row["rank"]) if pd.notna(row.get("rank")) else None,
            "clubelo_elo":        float(row["elo"]) if pd.notna(row.get("elo")) else None,
            "clubelo_country":    str(row.get("country", "")),
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
    dfp = _load_parquets("sofascore_match_team_stats__*.parquet")
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
        rows_out = []
        for _, row in results[summary_cols].head(10).iterrows():
            d = _row_to_dict(row)
            ce = _clubelo_team_context(str(row["team"]) if "team" in row.index else None)
            if ce:
                d.update(ce)
            rows_out.append(d)
        return {
            "count": len(results),
            "note": "Multiple results. Narrow with season/league/team or set full_stats=true.",
            "players": rows_out,
        }

    players = []
    for _, row in results.head(5).iterrows():
        d = _row_to_dict(row) if full_stats else _row_to_dict(row[summary_cols])
        team_nm = row.get("team") if "team" in row.index else None
        ce = _clubelo_team_context(str(team_nm) if team_nm is not None else None)
        if ce:
            d.update(ce)
        players.append(d)

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


def tool_get_sofascore_match(args: dict) -> dict:
    """
    SofaScore match-level data from collect_data.collect_sofascore_matches().
    Provide match_id, or home_team + away_team (optional league, season) to resolve id.
    """
    user_mid = args.get("match_id")
    home_q = args.get("home_team")
    away_q = args.get("away_team")
    league_f = args.get("league")
    season_f = args.get("season")
    include = (args.get("include") or "all").lower()
    period = (args.get("period") or "ALL").upper()
    limit = int(args.get("limit", 500))

    if user_mid is not None:
        mid = int(user_mid)
        lookup_method = "match_id"
    else:
        if not home_q or not away_q:
            return {
                "error": "Provide match_id (SofaScore int), or both home_team and away_team.",
            }
        resolved = _resolve_sofascore_match_id(str(home_q), str(away_q), league_f, season_f)
        if resolved is None:
            return {
                "error": (
                    f"No SofaScore match found for {home_q!r} vs {away_q!r}. "
                    "Run: python3 collect_data.py --sofascore-matches-only"
                ),
            }
        mid = resolved
        lookup_method = "team_names"

    out: dict = {"match_id": mid, "lookup_method": lookup_method}

    team_df = _load_parquets("sofascore_match_team_stats__*.parquet")
    if not team_df.empty and "match_id" in team_df.columns:
        trows = team_df[team_df["match_id"] == mid]
        if not trows.empty:
            prow = trows[trows["period"].astype(str).str.upper() == period]
            if prow.empty:
                prow = trows[trows["period"].astype(str).str.upper() == "ALL"]
            if not prow.empty:
                r0 = prow.iloc[0]
                out["home_team"] = r0.get("home_team")
                out["away_team"] = r0.get("away_team")
                out["league"] = r0.get("league")
                out["season"] = r0.get("season")

    want_all = include in ("all", "everything")
    any_rows = False

    if want_all or include == "shots":
        sdf = _load_parquets("sofascore_match_shots__*.parquet")
        if not sdf.empty and "match_id" in sdf.columns:
            sh = sdf[sdf["match_id"] == mid]
            out["shots_count"] = len(sh)
            out["shots"] = [_row_to_dict(r) for _, r in sh.head(limit).iterrows()]
            if len(out["shots"]) > 0:
                any_rows = True
        else:
            out["shots"] = []
            out["shots_count"] = 0

    if want_all or include == "team_stats":
        if team_df.empty:
            out["team_stats"] = []
        else:
            tsub = team_df[team_df["match_id"] == mid]
            if period != "ALL" and "period" in tsub.columns:
                tsub = tsub[tsub["period"].astype(str).str.upper() == period]
            out["team_stats"] = [_row_to_dict(r) for _, r in tsub.iterrows()]
            if len(out["team_stats"]) > 0:
                any_rows = True

    if want_all or include == "player_stats":
        pdf = _load_parquets("sofascore_match_player_stats__*.parquet")
        if not pdf.empty and "match_id" in pdf.columns:
            ps = pdf[pdf["match_id"] == mid]
            out["player_stats_count"] = len(ps)
            out["player_stats"] = [_row_to_dict(r) for _, r in ps.head(limit).iterrows()]
            if len(out["player_stats"]) > 0:
                any_rows = True
        else:
            out["player_stats"] = []
            out["player_stats_count"] = 0

    if want_all or include == "momentum":
        mdf = _load_parquets("sofascore_match_momentum__*.parquet")
        if not mdf.empty and "match_id" in mdf.columns:
            mm = mdf[mdf["match_id"] == mid]
            out["momentum"] = [_row_to_dict(r) for _, r in mm.iterrows()]
            if len(out["momentum"]) > 0:
                any_rows = True
        else:
            out["momentum"] = []

    if not any_rows:
        return {
            "error": (
                f"No SofaScore match data for id {mid}. "
                "Run: python3 collect_data.py --sofascore-matches-only"
            ),
        }

    return out


def tool_get_club_elo(args: dict) -> dict:
    """Latest ClubElo global ratings + upcoming fixtures (from collect_clubelo)."""
    team = args.get("team", "")
    if not team:
        return {"error": "team is required (club name, e.g. 'Liverpool' or 'Man City')."}

    ctx = _clubelo_team_context(str(team))
    if not ctx:
        return {
            "error": f"No ClubElo match for {team!r}. Run: python3 collect_data.py (ClubElo step) "
                     "or check spelling.",
        }

    out = {"team_query": team, **ctx}

    fix_paths = sorted(RAW_DIR.glob("clubelo__fixtures__*.parquet"))
    if fix_paths:
        try:
            fdf = pd.read_parquet(fix_paths[-1])
            tlow = team.lower()
            mask = (
                fdf["home_team"].astype(str).str.lower().str.contains(tlow, na=False)
                | fdf["away_team"].astype(str).str.lower().str.contains(tlow, na=False)
            )
            hits = fdf[mask].head(15)
            out["upcoming_fixtures_sample"] = [_row_to_dict(r) for _, r in hits.iterrows()]
            out["fixtures_file"] = fix_paths[-1].name
        except Exception as e:
            out["fixtures_error"] = str(e)

    globs = sorted(RAW_DIR.glob("clubelo__global__*.parquet"))
    if globs:
        out["global_ratings_file"] = globs[-1].name

    return out


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
            "sofascore_match_shots":   len(list(RAW_DIR.glob("sofascore_match_shots__*.parquet"))),
            "sofascore_match_team_stats": len(
                list(RAW_DIR.glob("sofascore_match_team_stats__*.parquet"))
            ),
            "sofascore_match_player_stats": len(
                list(RAW_DIR.glob("sofascore_match_player_stats__*.parquet"))
            ),
            "sofascore_match_momentum": len(
                list(RAW_DIR.glob("sofascore_match_momentum__*.parquet"))
            ),
            "clubelo_global":         len(list(RAW_DIR.glob("clubelo__global__*.parquet"))),
            "clubelo_fixtures":       len(list(RAW_DIR.glob("clubelo__fixtures__*.parquet"))),
            "transfermarkt_profiles":  len(list(RAW_DIR.glob("transfermarkt__*.parquet"))),
            "transfermarkt_mv_history":len(list(RAW_DIR.glob("transfermarkt_mv_history__*.parquet"))),
            "transfermarkt_transfers": len(list(RAW_DIR.glob("transfermarkt_transfers__*.parquet"))),
        }

    fresh = _freshness_summary()
    manifest_meta = _manifest_summary()

    if df.empty:
        out = {
            "status":    "NO DATA",
            "message":   "Run: python -m collect_data",
            "raw_files": raw_counts,
        }
        out.update(fresh)
        out.update(manifest_meta)
        return out

    status: dict = {
        "status":    "OK",
        "rows":      len(df),
        "columns":   len(df.columns),
        "leagues":   sorted(df["league"].unique().tolist()) if "league" in df.columns else [],
        "seasons":   sorted(df["season"].unique().tolist()) if "season" in df.columns else [],
        "raw_files": raw_counts,
    }
    status.update(fresh)
    status.update(manifest_meta)

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
                "match_id":   {"type": "integer", "description": "SofaScore event id"},
                "home_team":  {"type": "string",  "description": "Home club name (with away_team if no match_id)"},
                "away_team":  {"type": "string",  "description": "Away club name"},
                "league":     {"type": "string",  "description": "Optional filter when resolving by team names"},
                "season":     {"type": "string",  "description": "e.g. 2024-2025"},
                "include":    {"type": "string",  "description": "all | shots | team_stats | player_stats | momentum"},
                "period":     {"type": "string",  "description": "ALL | 1ST | 2ND for team_stats slice"},
                "limit":      {"type": "integer", "description": "Max rows for shots/player_stats (default 500)"},
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
            "for key stats, counts of supplementary parquet files, manifest build timestamps "
            "(last_built_at, oldest_source_fetched_at), and raw .freshness.json age."
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
            "serverInfo": {"name": "soccer-data", "version": "3.1"},
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
