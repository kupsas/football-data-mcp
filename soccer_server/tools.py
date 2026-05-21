"""MCP tool implementations (pure functions: ``args`` dict in, result dict out)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from collect_data.storage import freshness_summary, get_backend, manifest_summary
from soccer_server import db
from soccer_server.cache import get_unified
from soccer_server.data_loading import (
    _clubelo_team_context,
    _filter,
    _resolve_sofascore_match_id,
    _row_to_dict,
    _safe,
)
from soccer_server.errors import (
    generic_error,
    invalid_param_value_error,
    missing_param_error,
    missing_source_error,
    no_data_error,
    not_found_error,
)

def tool_get_player(args: dict) -> dict:
    df = get_unified()
    if df.empty:
        return no_data_error()

    name = args.get("name", "")
    season = args.get("season")
    league = args.get("league")
    team = args.get("team")
    full_stats = args.get("full_stats", True)

    results = _filter(df, player=name, season=season, league=league, team=team)
    if results.empty:
        return not_found_error("player", name)

    results = results.sort_values("season", ascending=False)

    summary_cols = [
        c
        for c in [
            "player",
            "team",
            "league",
            "season",
            "pos",
            "age",
            "nation",
            "goals",
            "assists",
            "xg",
            "xag",
            "npxg",
            "minutes",
            "games",
            "sofascore_rating",
            "market_value_eur",
            "contract_expiration",
        ]
        if c in results.columns
    ]

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
        "note": (
            "Showing up to 5 rows. Specify season/league/team to filter."
            if len(results) > 1
            else None
        ),
    }


def tool_scout_position(args: dict) -> dict:
    df = get_unified()
    if df.empty:
        return no_data_error()

    position = args.get("position", "FW")
    league = args.get("league")
    season = args.get("season")
    sort_by = args.get("sort_by")
    min_minutes = int(args.get("min_minutes", 900))
    max_age = args.get("max_age")
    max_value_eur = args.get("max_market_value_eur")
    limit = int(args.get("limit", 15))

    results = _filter(df, position=position, league=league, season=season, min_minutes=min_minutes)

    if max_age and "age_num" in results.columns:
        results = results[results["age_num"].fillna(99) <= float(max_age)]
    if max_value_eur and "market_value_eur" in results.columns:
        results = results[results["market_value_eur"].fillna(float("inf")) <= float(max_value_eur)]

    if results.empty:
        return generic_error("No players found for the given position and filters.")

    if not sort_by:
        pos_up = position.upper()
        if "GK" in pos_up:
            sort_by = next(
                (c for c in ["save_pct", "goals_against_per90", "clean_sheet_pct"] if c in results.columns),
                None,
            )
        elif "CB" in pos_up or ("DF" in pos_up and "FW" not in pos_up and "MF" not in pos_up):
            sort_by = next(
                (c for c in ["tackles", "interceptions", "aerials_won_pct"] if c in results.columns),
                None,
            )
        elif any(p in pos_up for p in ("MF", "DM", "CM")):
            sort_by = next(
                (c for c in ["progressive_passes", "key_passes", "xag"] if c in results.columns),
                None,
            )
        else:
            sort_by = next((c for c in ["goals", "xg", "xag"] if c in results.columns), None)

    if sort_by and sort_by not in results.columns:
        candidates = [
            c
            for c in results.columns
            if sort_by.lower() in c.lower() and results[c].dtype in (float, int, "float64", "int64")
        ]
        sort_by = candidates[0] if candidates else None

    results = results.nlargest(limit, sort_by) if sort_by else results.head(limit)

    base = ["player", "team", "league", "season", "pos", "age", "minutes"]
    extra = [sort_by] if sort_by else []
    extra += [
        "goals",
        "assists",
        "xg",
        "xag",
        "npxg",
        "sofascore_rating",
        "market_value_eur",
        "contract_expiration",
    ]
    seen: set[str] = set()
    show_cols: list[str] = []
    for c in base + extra:
        if c and c in results.columns and c not in seen:
            show_cols.append(c)
            seen.add(c)

    return {
        "position": position,
        "sort_by": sort_by or "default",
        "league": league or "All",
        "season": season or "All",
        "count": len(results),
        "players": [_row_to_dict(r) for _, r in results[show_cols].iterrows()],
    }


def tool_compare_players(args: dict) -> dict:
    df = get_unified()
    if df.empty:
        return no_data_error()

    names = args.get("names", [])
    season = args.get("season")
    stats = args.get("stats") or [
        "goals",
        "assists",
        "xg",
        "xag",
        "npxg",
        "xg_overperformance",
        "shots",
        "key_passes",
        "pass_completion_pct",
        "progressive_passes",
        "progressive_carries",
        "tackles_won",
        "interceptions",
        "sca",
        "gca",
        "aerials_won_pct",
        "sofascore_rating",
        "minutes",
        "games",
        "market_value_eur",
        "contract_expiration",
    ]

    comparisons = []
    for name in names:
        results = _filter(df, player=name, season=season)
        if results.empty:
            comparisons.append({"player": name, **not_found_error("player", name)})
            continue
        row = results.sort_values("season", ascending=False).iloc[0]
        d = {
            "player": row.get("player", name),
            "team": row.get("team"),
            "league": row.get("league"),
            "season": row.get("season"),
        }
        for stat in stats:
            if stat in row.index:
                d[stat] = _safe(row[stat])
        comparisons.append(d)

    return {"comparisons": comparisons}


def tool_find_similar_players(args: dict) -> dict:
    df = get_unified()
    if df.empty:
        return no_data_error()

    name = args.get("name", "")
    season = args.get("season")
    n_results = int(args.get("n", 10))
    min_minutes = int(args.get("min_minutes", 900))
    max_value_eur = args.get("max_market_value_eur")
    same_league = bool(args.get("same_league", False))

    target_rows = _filter(df, player=name, season=season)
    if target_rows.empty:
        return not_found_error("player", name)
    target = target_rows.sort_values("season", ascending=False).iloc[0]

    preferred = [
        "xg",
        "npxg",
        "xag",
        "xg_chain",
        "xg_buildup",
        "shots",
        "pass_completion_pct",
        "progressive_passes",
        "progressive_carries",
        "tackles_won",
        "interceptions",
        "sca",
        "gca",
        "aerials_won_pct",
        "key_passes",
        "touches",
    ]
    exclude = {
        "player",
        "team",
        "league",
        "season",
        "pos",
        "age",
        "nation",
        "player_id",
        "team_id",
        "understat_id",
        "tm_id",
        "born",
        "age_num",
        "minutes",
        "games",
        "starts",
        "ninety_s",
        "market_value_eur",
        "goals",
        "assists",
    }
    stat_cols = [
        c
        for c in df.columns
        if c not in exclude
        and not c.startswith("_")
        and df[c].dtype in (float, int, "float64", "int64")
        and df[c].max() > 0
    ]
    stat_cols_use = [c for c in preferred if c in stat_cols]
    stat_cols_use += [c for c in stat_cols if c not in stat_cols_use]
    stat_cols_use = stat_cols_use[:15]

    if not stat_cols_use:
        return generic_error("No numeric stat columns found for comparison.")

    pool = _filter(df, season=season, min_minutes=min_minutes)
    if same_league and "league" in target.index:
        pool = pool[pool["league"] == target["league"]]
    pool = pool[~pool["_player_lower"].str.contains(name.lower(), na=False)]
    if max_value_eur and "market_value_eur" in pool.columns:
        pool = pool[pool["market_value_eur"].fillna(float("inf")) <= float(max_value_eur)]

    target_vals = np.array([_safe(target.get(c, 0)) or 0 for c in stat_cols_use], dtype=float)
    pool_vals = pool[stat_cols_use].fillna(0).values.astype(float)

    stds = pool_vals.std(axis=0)
    stds[stds == 0] = 1
    target_norm = target_vals / stds
    pool_norm = pool_vals / stds

    a_norm = np.linalg.norm(target_norm)
    if a_norm == 0:
        return generic_error("Target player has no stat data for similarity calculation.")
    b_norms = np.linalg.norm(pool_norm, axis=1)
    b_norms[b_norms == 0] = 1
    sims = (pool_norm @ target_norm) / (b_norms * a_norm)

    pool = pool.copy()
    pool["_similarity"] = sims
    top = pool.nlargest(n_results, "_similarity")

    cols = [
        c
        for c in [
            "player",
            "team",
            "league",
            "season",
            "pos",
            "goals",
            "assists",
            "xg",
            "minutes",
            "market_value_eur",
            "_similarity",
        ]
        if c in top.columns
    ]

    return {
        "target": f"{target.get('player')} ({target.get('team')}, {target.get('season')})",
        "similar_players": [_row_to_dict(r) for _, r in top[cols].iterrows()],
    }


def tool_get_league_table(args: dict) -> dict:
    league = args.get("league", "")
    season = args.get("season", "2024-2025")
    split = args.get("split", "overall")

    if split not in ("overall", "home", "away"):
        split = "overall"

    slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
    be = get_backend()
    rel = f"raw/understat_league_table__{slug}__{split}.parquet"
    if not be.exists_rel(rel):
        available = be.list_raw_glob("understat_league_table__*.parquet")
        err = missing_source_error(
            f"League table for {league!r} {season} ({split})",
            "python -m collect_data --understat-tables-only",
        )
        err["available_files"] = available[:20]
        return err

    df = be.read_parquet_rel(rel)
    return {
        "league": league,
        "season": season,
        "split": split,
        "table": [_row_to_dict(r) for _, r in df.iterrows()],
    }


def tool_get_match(args: dict) -> dict:
    match_id = args.get("match_id")
    include = args.get("include", "both")

    if not match_id:
        return missing_param_error("match_id")

    mid = int(match_id)
    result: dict = {"match_id": mid}

    if not db.table_empty("understat_match_info"):
        info_df = db.query(
            "SELECT * FROM understat_match_info WHERE match_id = ?",
            [mid],
        )
        if not info_df.empty:
            result["match_info"] = _row_to_dict(info_df.iloc[0])

    if include in ("shots", "both"):
        if db.table_empty("understat_match_shots"):
            result["shots"] = []
        else:
            shots_df = db.query(
                "SELECT * FROM understat_match_shots WHERE match_id = ?",
                [mid],
            )
            result["shots"] = [_row_to_dict(r) for _, r in shots_df.iterrows()]
            result["shots_count"] = len(shots_df)

    if include in ("rosters", "both"):
        if db.table_empty("understat_rosters"):
            result["rosters"] = []
        else:
            rosters_df = db.query(
                "SELECT * FROM understat_rosters WHERE match_id = ?",
                [mid],
            )
            result["rosters"] = [_row_to_dict(r) for _, r in rosters_df.iterrows()]
            result["roster_count"] = len(rosters_df)

    if not result.get("match_info") and not result.get("shots") and not result.get("rosters"):
        return missing_source_error(
            f"Understat match data for match_id {mid}",
            "python -m collect_data --understat-matches-only",
        )

    return result


def tool_get_sofascore_match(args: dict) -> dict:
    """SofaScore match-level data (collect_data --sofascore-matches-only or full run)."""
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
            return missing_param_error("match_id or home_team+away_team")
        resolved = _resolve_sofascore_match_id(str(home_q), str(away_q), league_f, season_f)
        if resolved is None:
            return missing_source_error(
                f"SofaScore match for {home_q!r} vs {away_q!r}",
                "python -m collect_data --sofascore-matches-only",
            )
        mid = resolved
        lookup_method = "team_names"

    out: dict = {"match_id": mid, "lookup_method": lookup_method}

    if not db.table_empty("sofascore_match_team_stats"):
        trows = db.query(
            "SELECT * FROM sofascore_match_team_stats WHERE match_id = ?",
            [mid],
        )
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
        if db.table_empty("sofascore_match_shots"):
            out["shots"] = []
            out["shots_count"] = 0
        else:
            sh = db.query(
                "SELECT * FROM sofascore_match_shots WHERE match_id = ? LIMIT ?",
                [mid, limit],
            )
            total = db.query_scalar(
                "SELECT count(*) FROM sofascore_match_shots WHERE match_id = ?",
                [mid],
            )
            out["shots_count"] = int(total or 0)
            out["shots"] = [_row_to_dict(r) for _, r in sh.iterrows()]
            if out["shots"]:
                any_rows = True

    if want_all or include == "team_stats":
        if db.table_empty("sofascore_match_team_stats"):
            out["team_stats"] = []
        else:
            if period != "ALL":
                tsub = db.query(
                    """
                    SELECT * FROM sofascore_match_team_stats
                    WHERE match_id = ? AND upper(CAST(period AS VARCHAR)) = ?
                    """,
                    [mid, period],
                )
            else:
                tsub = db.query(
                    "SELECT * FROM sofascore_match_team_stats WHERE match_id = ?",
                    [mid],
                )
            out["team_stats"] = [_row_to_dict(r) for _, r in tsub.iterrows()]
            if out["team_stats"]:
                any_rows = True

    if want_all or include == "player_stats":
        if db.table_empty("sofascore_match_player_stats"):
            out["player_stats"] = []
            out["player_stats_count"] = 0
        else:
            ps = db.query(
                "SELECT * FROM sofascore_match_player_stats WHERE match_id = ? LIMIT ?",
                [mid, limit],
            )
            total = db.query_scalar(
                "SELECT count(*) FROM sofascore_match_player_stats WHERE match_id = ?",
                [mid],
            )
            out["player_stats_count"] = int(total or 0)
            out["player_stats"] = [_row_to_dict(r) for _, r in ps.iterrows()]
            if out["player_stats"]:
                any_rows = True

    if want_all or include == "momentum":
        if db.table_empty("sofascore_match_momentum"):
            out["momentum"] = []
        else:
            mm = db.query(
                "SELECT * FROM sofascore_match_momentum WHERE match_id = ?",
                [mid],
            )
            out["momentum"] = [_row_to_dict(r) for _, r in mm.iterrows()]
            if out["momentum"]:
                any_rows = True

    if not any_rows:
        return missing_source_error(
            f"SofaScore match data for id {mid}",
            "python -m collect_data --sofascore-matches-only",
        )

    return out


def tool_get_club_elo(args: dict) -> dict:
    """Latest ClubElo global ratings + upcoming fixtures (from collect_clubelo)."""
    team = args.get("team", "")
    if not team:
        return missing_param_error("team")

    ctx = _clubelo_team_context(str(team))
    if not ctx:
        return missing_source_error(
            f"ClubElo entry for {team!r}",
            "python -m collect_data (include ClubElo step) or check spelling.",
        )

    out: dict = {"team_query": team, **ctx}
    be = get_backend()
    if not db.table_empty("clubelo_fixtures"):
        try:
            tlow = f"%{team.lower()}%"
            hits = db.query(
                """
                SELECT * FROM clubelo_fixtures
                WHERE lower(CAST(home_team AS VARCHAR)) LIKE ?
                   OR lower(CAST(away_team AS VARCHAR)) LIKE ?
                LIMIT 15
                """,
                [tlow, tlow],
            )
            out["upcoming_fixtures_sample"] = [_row_to_dict(r) for _, r in hits.iterrows()]
        except Exception as e:
            out["fixtures_error"] = str(e)

    globs = be.list_raw_glob("clubelo__global__*.parquet")
    if globs:
        out["global_ratings_file"] = sorted(globs)[-1]

    return out


def tool_get_player_history(args: dict) -> dict:
    name = args.get("name", "")
    history_type = args.get("type", "form")
    season = args.get("season")
    league = args.get("league")
    limit = int(args.get("limit", 50))

    if history_type not in ("form", "value", "transfers"):
        return invalid_param_value_error("type must be 'form', 'value', or 'transfers'")

    if history_type == "form":
        if db.table_empty("understat_rosters"):
            return missing_source_error(
                "Understat roster (match-level) data",
                "python -m collect_data --understat-matches-only",
            )

        base_clauses = ["lower(CAST(player AS VARCHAR)) LIKE ?"]
        params: list = [f"%{name.lower()}%"]
        if season:
            base_clauses.append("season = ?")
            params.append(season)
        if league:
            base_clauses.append("lower(league) LIKE ?")
            params.append(f"%{league.lower()}%")
        where_base = " AND ".join(base_clauses)
        if db.table_empty("understat_match_info"):
            player_rows = db.query(
                f"SELECT * FROM understat_rosters WHERE {where_base}",
                params,
            )
        else:
            alias_clauses = [
                c.replace("player", "r.player")
                .replace("season", "r.season")
                .replace("league", "r.league")
                for c in base_clauses
            ]
            where_alias = " AND ".join(alias_clauses)
            player_rows = db.query(
                f"""
                SELECT r.*, i.home_team, i.away_team, i.datetime, i.home_goals, i.away_goals
                FROM understat_rosters r
                LEFT JOIN understat_match_info i ON r.match_id = i.match_id
                WHERE {where_alias}
                """,
                params,
            )
        if player_rows.empty:
            return not_found_error("match-level rows for player", name)

        return {
            "player": name,
            "type": "form",
            "records": len(player_rows),
            "matches": [_row_to_dict(r) for _, r in player_rows.head(limit).iterrows()],
        }

    tm_id = None
    df = get_unified()
    if not df.empty and "tm_id" in df.columns:
        candidates = _filter(df, player=name, season=season, league=league)
        if not candidates.empty:
            val = candidates.sort_values("season", ascending=False).iloc[0].get("tm_id")
            if pd.notna(val) and str(val).strip():
                tm_id = str(val).strip()

    if history_type == "value":
        if db.table_empty("transfermarkt_mv_history"):
            mv_df = pd.DataFrame()
        else:
            mv_df = db.query("SELECT * FROM transfermarkt_mv_history")
        if mv_df.empty:
            return missing_source_error(
                "Transfermarkt market value history",
                "python -m collect_data --transfermarkt-only",
            )

        if tm_id:
            rows = mv_df[mv_df["tm_id"].astype(str) == tm_id]
        else:
            if db.table_empty("transfermarkt_profiles"):
                tm_flat = pd.DataFrame()
            else:
                tm_flat = db.query("SELECT * FROM transfermarkt_profiles")
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
            return generic_error(
                f"No market value history for {name!r}.",
                hint="python -m collect_data --transfermarkt-only",
            )

        return {
            "player": name,
            "tm_id": tm_id,
            "type": "value",
            "history": [_row_to_dict(r) for _, r in rows.sort_values("date").head(limit).iterrows()],
        }

    if db.table_empty("transfermarkt_transfers"):
        tr_df = pd.DataFrame()
    else:
        tr_df = db.query("SELECT * FROM transfermarkt_transfers")
    if tr_df.empty:
        return missing_source_error(
            "Transfermarkt transfer history",
            "python -m collect_data --transfermarkt-only",
        )

    if tm_id:
        rows = tr_df[tr_df["tm_id"].astype(str) == tm_id]
    else:
        rows = tr_df[tr_df["tm_name"].astype(str).str.lower().str.contains(name.lower(), na=False)]

    if rows.empty:
        return not_found_error("transfer history for player", name)

    return {
        "player": name,
        "tm_id": tm_id,
        "type": "transfers",
        "transfers": [_row_to_dict(r) for _, r in rows.drop_duplicates().head(limit).iterrows()],
    }


def _coverage_pct(series: pd.Series) -> int:
    """Return 0–100 coverage for one key_stat column within a season slice."""
    n = len(series)
    if n == 0:
        return 0
    if (
        series.dtype == object
        or pd.api.types.is_string_dtype(series.dtype)
        or not pd.api.types.is_numeric_dtype(series.dtype)
    ):
        return int(100 * series.notna().sum() / n)
    numeric = pd.to_numeric(series, errors="coerce")
    return int(100 * (numeric.fillna(0) > 0).sum() / n)


def tool_data_status(args: dict) -> dict:
    del args  # unused; schema allows empty object
    df = get_unified()
    be = get_backend()

    raw_counts = {
        "understat_league_tables": len(be.list_raw_glob("understat_league_table__*.parquet")),
        "understat_match_info": len(be.list_raw_glob("understat_match_info__*.parquet")),
        "understat_match_shots": len(be.list_raw_glob("understat_match_shots__*.parquet")),
        "understat_rosters": len(be.list_raw_glob("understat_rosters__*.parquet")),
        "sofascore_match_shots": len(be.list_raw_glob("sofascore_match_shots__*.parquet")),
        "sofascore_match_team_stats": len(be.list_raw_glob("sofascore_match_team_stats__*.parquet")),
        "sofascore_match_player_stats": len(
            be.list_raw_glob("sofascore_match_player_stats__*.parquet")
        ),
        "sofascore_match_momentum": len(be.list_raw_glob("sofascore_match_momentum__*.parquet")),
        "clubelo_global": len(be.list_raw_glob("clubelo__global__*.parquet")),
        "clubelo_fixtures": len(be.list_raw_glob("clubelo__fixtures__*.parquet")),
        "transfermarkt_profiles": len(
            [
                n
                for n in be.list_raw_glob("transfermarkt__*.parquet")
                if "mv_history" not in n and "transfers" not in n
            ]
        ),
        "transfermarkt_mv_history": len(be.list_raw_glob("transfermarkt_mv_history__*.parquet")),
        "transfermarkt_transfers": len(be.list_raw_glob("transfermarkt_transfers__*.parquet")),
    }

    fresh = freshness_summary(be)
    manifest_meta = manifest_summary(be)

    if df.empty:
        out = {
            "status": "NO DATA",
            "message": "No unified player table is loaded.",
            "hint": "Run: python -m collect_data",
            "raw_files": raw_counts,
        }
        out.update(fresh)
        out.update(manifest_meta)
        return out

    status: dict = {
        "status": "OK",
        "rows": len(df),
        "columns": len(df.columns),
        "leagues": sorted(df["league"].unique().tolist()) if "league" in df.columns else [],
        "seasons": sorted(df["season"].unique().tolist()) if "season" in df.columns else [],
        "raw_files": raw_counts,
    }
    status.update(fresh)
    status.update(manifest_meta)

    key_stats = [
        "goals",
        "xg",
        "npxg",
        "xag",
        "tackles_won",
        "interceptions",
        "aerials_won_pct",
        "pass_completion_pct",
        "sofascore_rating",
        "market_value_eur",
        "contract_expiration",
    ]
    coverage: dict = {}
    if "season" in df.columns:
        for season in sorted(df["season"].unique()):
            s = df[df["season"] == season]
            cov: dict = {"players": len(s)}
            for stat in key_stats:
                if stat in s.columns:
                    cov[stat] = f"{_coverage_pct(s[stat])}%"
            coverage[season] = cov
    status["coverage"] = coverage
    status["query_engine"] = "duckdb"
    status["analytics_views"] = {
        name: int(db.query_scalar(f"SELECT count(*) FROM {name}") or 0)
        for name in (
            "player_match_log",
            "player_form_profile",
            "team_season_stats",
            "player_shot_profile",
            "match_index",
        )
    }

    return status


def tool_get_player_match_log(args: dict) -> dict:
    """Per-match SofaScore stats for one player (from player_match_log view)."""
    name = args.get("name", "")
    if not name:
        return missing_param_error("name")
    season = args.get("season")
    league = args.get("league")
    team = args.get("team")
    limit = int(args.get("limit", 20))
    home_only = args.get("home_only")
    away_only = args.get("away_only")

    if db.table_empty("player_match_log"):
        return missing_source_error(
            "SofaScore match-level player data",
            "python -m collect_data --sofascore-matches-only",
        )

    clauses = ["lower(player_name) LIKE ?"]
    params: list[Any] = [f"%{name.lower()}%"]
    if season:
        clauses.append("season = ?")
        params.append(season)
    if league:
        clauses.append("lower(league) LIKE ?")
        params.append(f"%{league.lower()}%")
    if team:
        clauses.append("lower(team) LIKE ?")
        params.append(f"%{team.lower()}%")
    if home_only:
        clauses.append("is_home = true")
    if away_only:
        clauses.append("COALESCE(is_home, false) = false")

    rows = db.query(
        f"""
        SELECT * FROM player_match_log
        WHERE {' AND '.join(clauses)}
        ORDER BY match_id DESC
        LIMIT ?
        """,
        [*params, limit],
    )
    if rows.empty:
        return not_found_error("match log for player", name)

    return {
        "player": name,
        "matches_returned": len(rows),
        "match_log": [_row_to_dict(r) for _, r in rows.iterrows()],
    }


def tool_get_player_form(args: dict) -> dict:
    """Aggregated form metrics from match-level SofaScore ratings."""
    name = args.get("name", "")
    if not name:
        return missing_param_error("name")
    season = args.get("season")
    league = args.get("league")
    team = args.get("team")
    limit = int(args.get("limit", 10))

    if db.table_empty("player_form_profile"):
        return missing_source_error(
            "Player form aggregates (requires SofaScore match player stats)",
            "python -m collect_data --sofascore-matches-only",
        )

    clauses = ["lower(player_name) LIKE ?"]
    params: list[Any] = [f"%{name.lower()}%"]
    if season:
        clauses.append("season = ?")
        params.append(season)
    if league:
        clauses.append("lower(league) LIKE ?")
        params.append(f"%{league.lower()}%")
    if team:
        clauses.append("lower(team) LIKE ?")
        params.append(f"%{team.lower()}%")

    rows = db.query(
        f"""
        SELECT * FROM player_form_profile
        WHERE {' AND '.join(clauses)}
        ORDER BY season DESC, matches_played DESC
        LIMIT ?
        """,
        [*params, limit],
    )
    if rows.empty:
        return not_found_error("form profile for player", name)

    # Last N match ratings for trend
    last_matches = pd.DataFrame()
    if not db.table_empty("player_match_log"):
        last_matches = db.query(
            f"""
            SELECT match_id, rating, goals, assists, xg, opponent, is_home, team
            FROM player_match_log
            WHERE {' AND '.join(clauses)}
              AND rating IS NOT NULL AND rating > 0
            ORDER BY match_id DESC
            LIMIT 5
            """,
            params,
        )

    out: dict = {
        "player": name,
        "profiles": [_row_to_dict(r) for _, r in rows.iterrows()],
    }
    if not last_matches.empty:
        out["last_5_matches"] = [_row_to_dict(r) for _, r in last_matches.iterrows()]
        out["last_5_avg_rating"] = round(float(last_matches["rating"].mean()), 3)
    return out


def tool_get_team_stats(args: dict) -> dict:
    """Aggregated team season stats from SofaScore match team data."""
    team = args.get("team", "")
    if not team:
        return missing_param_error("team")
    season = args.get("season")
    league = args.get("league")
    limit = int(args.get("limit", 5))

    if db.table_empty("team_season_stats"):
        return missing_source_error(
            "Team season aggregates (SofaScore match team stats)",
            "python -m collect_data --sofascore-matches-only",
        )

    clauses = ["lower(team) LIKE ?"]
    params: list[Any] = [f"%{team.lower()}%"]
    if season:
        clauses.append("season = ?")
        params.append(season)
    if league:
        clauses.append("lower(league) LIKE ?")
        params.append(f"%{league.lower()}%")

    rows = db.query(
        f"""
        SELECT * FROM team_season_stats
        WHERE {' AND '.join(clauses)}
        ORDER BY season DESC, matches DESC
        LIMIT ?
        """,
        [*params, limit],
    )
    if rows.empty:
        return not_found_error("team stats for", team)

    # Attach ClubElo context when available
    result: dict = {
        "team_query": team,
        "stats": [_row_to_dict(r) for _, r in rows.iterrows()],
    }
    ce = _clubelo_team_context(team)
    if ce:
        result["clubelo"] = ce
    return result


def tool_compare_teams(args: dict) -> dict:
    """Side-by-side team season aggregates for two or more clubs."""
    names = args.get("names", [])
    if not names or len(names) < 2:
        return missing_param_error("names (list of at least 2 team names)")
    season = args.get("season")
    league = args.get("league")

    if db.table_empty("team_season_stats"):
        return missing_source_error(
            "Team season aggregates",
            "python -m collect_data --sofascore-matches-only",
        )

    comparisons = []
    for team_name in names:
        clauses = ["lower(team) LIKE ?"]
        params: list[Any] = [f"%{str(team_name).lower()}%"]
        if season:
            clauses.append("season = ?")
            params.append(season)
        if league:
            clauses.append("lower(league) LIKE ?")
            params.append(f"%{league.lower()}%")
        hit = db.query(
            f"""
            SELECT * FROM team_season_stats
            WHERE {' AND '.join(clauses)}
            ORDER BY season DESC
            LIMIT 1
            """,
            params,
        )
        if hit.empty:
            comparisons.append({"team": team_name, **not_found_error("team", team_name)})
        else:
            row = hit.iloc[0]
            d = _row_to_dict(row)
            d["team_query"] = team_name
            comparisons.append(d)

    return {"comparisons": comparisons}


def tool_search_matches(args: dict) -> dict:
    """Search SofaScore matches by team, league, season; optional sort by total xG."""
    team = args.get("team")
    league = args.get("league")
    season = args.get("season")
    limit = int(args.get("limit", 20))
    sort_by = (args.get("sort_by") or "xg_total").lower()

    if db.table_empty("match_index"):
        return missing_source_error(
            "Match index (SofaScore team stats)",
            "python -m collect_data --sofascore-matches-only",
        )

    clauses = ["1=1"]
    params: list[Any] = []
    if team:
        t = f"%{team.lower()}%"
        clauses.append("(lower(home_team) LIKE ? OR lower(away_team) LIKE ?)")
        params.extend([t, t])
    if league:
        clauses.append("lower(league) LIKE ?")
        params.append(f"%{league.lower()}%")
    if season:
        clauses.append("season = ?")
        params.append(season)

    order = "xg_home + xg_away DESC"
    if sort_by == "shots":
        order = "shots_home + shots_away DESC"
    elif sort_by == "possession":
        order = "GREATEST(possession_home, possession_away) DESC"

    rows = db.query(
        f"""
        SELECT *,
               COALESCE(xg_home, 0) + COALESCE(xg_away, 0) AS xg_total
        FROM match_index
        WHERE {' AND '.join(clauses)}
        ORDER BY {order}
        LIMIT ?
        """,
        [*params, limit],
    )

    return {
        "count": len(rows),
        "matches": [_row_to_dict(r) for _, r in rows.iterrows()],
    }


def tool_get_player_shot_map(args: dict) -> dict:
    """Shot locations and metadata for a player (SofaScore match shots)."""
    name = args.get("name", "")
    if not name:
        return missing_param_error("name")
    season = args.get("season")
    league = args.get("league")
    limit = int(args.get("limit", 100))

    if db.table_empty("sofascore_match_shots"):
        return missing_source_error(
            "SofaScore shot map data",
            "python -m collect_data --sofascore-matches-only",
        )

    clauses = ["lower(player_name) LIKE ?"]
    params: list[Any] = [f"%{name.lower()}%"]
    if season:
        clauses.append("season = ?")
        params.append(season)
    if league:
        clauses.append("lower(league) LIKE ?")
        params.append(f"%{league.lower()}%")

    shots = db.query(
        f"""
        SELECT match_id, player_name, minute, shot_type, situation, body_part,
               xg, xgot, player_x, player_y, goal_mouth_x, goal_mouth_y, league, season
        FROM sofascore_match_shots
        WHERE {' AND '.join(clauses)}
        ORDER BY match_id DESC, minute DESC
        LIMIT ?
        """,
        [*params, limit],
    )
    if shots.empty:
        return not_found_error("shots for player", name)

    profile = pd.DataFrame()
    if not db.table_empty("player_shot_profile"):
        profile = db.query(
            f"""
            SELECT * FROM player_shot_profile
            WHERE {' AND '.join(clauses)}
            ORDER BY season DESC
            LIMIT 3
            """,
            params,
        )

    return {
        "player": name,
        "shots_returned": len(shots),
        "shots": [_row_to_dict(r) for _, r in shots.iterrows()],
        "season_profile": [_row_to_dict(r) for _, r in profile.iterrows()] if not profile.empty else [],
    }
