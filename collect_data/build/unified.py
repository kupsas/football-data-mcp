"""Build ``unified_player_stats`` from raw parquet layers (Understat, SofaScore, financials)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from collect_data.config import (
    MANUAL_SS_OVERRIDES,
    SEASON_TOTAL_LEAGUE,
    UNDERSTAT_AUTHORITATIVE,
    UNDERSTAT_LEAGUES,
    XG_SOURCE_SOFASCORE,
    XG_SOURCE_UNDERSTAT,
)
from collect_data.helpers import _norm_name, _norm_team, sanitize_player_name
from collect_data.build.eafc import merge_eafc_data
from collect_data.build.financials import merge_financial_data
from collect_data.storage import DATA_DIR, get_backend, load_parquets

log = logging.getLogger(__name__)

REEP_PEOPLE_PATH = DATA_DIR / "reference" / "reep_people.csv"

# Fuzzy SofaScore↔Understat name match (same league+season bucket).
_FUZZY_SS_IN_CLUB_CUTOFF = 75
_FUZZY_SS_PLAYER_ONLY_CUTOFF = 92
_FUZZY_SS_CLUB_CUTOFF = 78

def load_all_understat_raw() -> pd.DataFrame:
    return load_parquets("understat__*.parquet")


def load_all_sofascore_raw() -> pd.DataFrame:
    return load_parquets("sofascore__*.parquet")


# ══════════════════════════════════════════════════════════════════════════════
#  Build helpers
# ══════════════════════════════════════════════════════════════════════════════

def _build_base_from_understat(us_df: pd.DataFrame) -> pd.DataFrame:
    """Build one row per (player, league, season) from Understat data."""
    us_dedup = (
        us_df.sort_values("us_minutes", ascending=False)
        .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first")
        .copy()
    )
    rename = {
        "id": "understat_id", "player": "player", "us_team": "team",
        "us_pos": "pos", "us_games": "games", "us_minutes": "minutes",
        "us_goals": "goals", "us_assists": "assists", "us_shots": "shots",
        "us_key_passes": "key_passes", "us_yellow_cards": "yellow_cards",
        "us_red_cards": "red_cards", "us_npg": "npg",
        "league": "league", "season": "season",
        "xg": "xg", "xag": "xag", "npxg": "npxg",
        "xg_chain": "xg_chain", "xg_buildup": "xg_buildup",
    }
    cols = {k: v for k, v in rename.items() if k in us_dedup.columns}
    base = us_dedup[list(cols)].rename(columns=cols).copy()
    for col in ["games","minutes","goals","assists","shots","key_passes",
                "yellow_cards","red_cards","npg","xg","xag","npxg",
                "xg_chain","xg_buildup"]:
        if col in base.columns:
            base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0)
    base["ninety_s"] = (base["minutes"] / 90).round(2)
    return base


def _build_base_from_sofascore(ss_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one row per (player, league, season) from SofaScore data.
    Used for non-Big5 leagues that have no Understat coverage.
    """
    if "_name_norm" not in ss_df.columns:
        ss_df = ss_df.copy()
        ss_df["_name_norm"] = ss_df["player"].apply(_norm_name)

    ss_dedup = (
        ss_df.sort_values("minutes" if "minutes" in ss_df.columns else ss_df.columns[0],
                          ascending=False)
        .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first")
        .copy()
    )
    return ss_dedup.drop(columns=["_name_norm", "sofascore_team_id"], errors="ignore")


def _merge_understat_into(unified: pd.DataFrame, us_df: pd.DataFrame) -> pd.DataFrame:
    """Merge Understat xG/xA columns into an existing unified frame (SofaScore-only base)."""
    if us_df.empty:
        return unified
    log.info(f"  Merging Understat: {len(us_df)} records, {us_df['league'].nunique()} leagues")
    unified["_name_norm"] = unified["player"].apply(_norm_name)
    if "_name_norm" not in us_df.columns:
        us_df["_name_norm"] = us_df["player"].apply(_norm_name)

    us_cols = ["xg","xag","npxg","xg_chain","xg_buildup","us_key_passes","us_npg","id"]
    us_merge = (
        us_df[["_name_norm","league","season"] + [c for c in us_cols if c in us_df.columns]]
        .sort_values("xg", ascending=False)
        .drop_duplicates(subset=["_name_norm","league","season"], keep="first")
    )
    unified = unified.merge(us_merge, on=["_name_norm","league","season"], how="left")

    if "us_key_passes" in unified.columns:
        if "key_passes" not in unified.columns:
            unified = unified.rename(columns={"us_key_passes": "key_passes"})
        else:
            mask = unified["key_passes"].isna() | (unified["key_passes"] == 0)
            unified.loc[mask, "key_passes"] = unified.loc[mask, "us_key_passes"]
            unified = unified.drop(columns=["us_key_passes"])
    if "us_npg" in unified.columns:
        unified = unified.rename(columns={"us_npg": "npg"})
    if "id" in unified.columns:
        unified = unified.rename(columns={"id": "understat_id"})
    unified = unified.drop(columns=["_name_norm"], errors="ignore")

    matched = (unified["xg"].fillna(0) > 0).sum()
    log.info(f"  xG coverage: {matched}/{len(unified)} ({100*matched//len(unified) if unified.shape[0] else 0}%)")
    return unified


def _load_reep_understat_to_sofascore() -> dict[int, int]:
    """
    REEP crosswalk: Understat player id -> SofaScore player id.

    See ``data/reference/reep_people.csv`` from the REEP project.
    """
    if not REEP_PEOPLE_PATH.exists():
        log.warning(
            "REEP people.csv missing at %s; SofaScore merge falls back to name+fuzzy only",
            REEP_PEOPLE_PATH,
        )
        return {}

    reep = pd.read_csv(
        REEP_PEOPLE_PATH,
        usecols=["key_understat", "key_sofascore"],
        low_memory=False,
    )
    both = reep.dropna(subset=["key_understat", "key_sofascore"]).copy()
    both["key_understat"] = pd.to_numeric(both["key_understat"], errors="coerce")
    both["key_sofascore"] = pd.to_numeric(both["key_sofascore"], errors="coerce")
    both = both.dropna(subset=["key_understat", "key_sofascore"])
    both["key_understat"] = both["key_understat"].astype(int)
    both["key_sofascore"] = both["key_sofascore"].astype(int)
    both = both.drop_duplicates(subset=["key_understat"], keep="first")
    return dict(zip(both["key_understat"], both["key_sofascore"]))


def _sofascore_priority_column_names() -> list[str]:
    """SofaScore columns to merge into Big 5 Understat rows (order matters for dedupe)."""
    return [
        "sofascore_id", "sofascore_team_id", "sofascore_rating",
        "sofascore_rating_total", "sofascore_rating_count", "totw_appearances",
        "starts", "tackles", "tackles_won", "tackles_won_pct",
        "interceptions", "clearances", "blocked_shots", "outfield_blocks",
        "errors_leading_to_goal", "errors_leading_to_shot", "dribbled_past",
        "aerials_won", "aerials_won_pct", "aerials_lost",
        "dribbles_completed", "dribbles_pct", "dribbles_attempted",
        "ground_duels_won", "ground_duels_won_pct",
        "duels_won", "duels_won_pct", "duels_lost",
        "passes_total", "passes_completed", "passes_inaccurate", "pass_completion_pct",
        "passes_final_third", "passes_opp_half", "passes_own_half",
        "passes_opp_half_total", "passes_own_half_total",
        "long_balls_total", "long_balls_completed", "long_balls_pct",
        "crosses_total", "crosses_completed", "crosses_pct",
        "chipped_passes_total", "chipped_passes_completed",
        "pass_to_assist", "attempt_assists", "big_chances_created",
        "big_chances_missed", "goals_inside_box", "goals_outside_box",
        "goals_headed", "goals_left_foot", "goals_right_foot", "goals_penalty",
        "goals_freekick", "own_goals", "goals_assists", "hit_woodwork",
        "shots_inside_box", "shots_outside_box", "shots_on_target", "shots_off_target",
        "shots_set_piece",
        "goal_conversion_pct", "scoring_frequency", "set_piece_conversion",
        "touches", "possession_lost", "possession_won_att_third",
        "dispossessed", "ball_recoveries", "fouls", "fouled", "offsides",
        "yellow_red_cards", "direct_red_cards",
        "pens_taken", "pens_conceded", "pens_won", "pen_conversion_pct",
        "pen_miss", "pen_post", "pen_on_target",
        "saves", "saves_caught", "saves_parried",
        "saves_inside_box", "saves_outside_box",
        "goals_conceded", "goals_conceded_inside_box", "goals_conceded_outside_box",
        "goals_prevented", "clean_sheets", "high_claims",
        "crosses_not_claimed", "punches", "runs_out", "runs_out_successful",
        "goal_kicks", "pens_saved", "pens_faced",
    ]


def _sofascore_bring_columns(
    unified: pd.DataFrame, ss_existing: pd.DataFrame
) -> list[str]:
    """Columns to pull from SofaScore into unified (excludes Understat-authoritative)."""
    have_already = set(unified.columns) | UNDERSTAT_AUTHORITATIVE
    merge_key = {"_name_norm", "_name_norm_ss", "league", "season"}
    ss_new_cols = [
        c for c in ss_existing.columns
        if c not in have_already and c not in merge_key
    ]
    priority = _sofascore_priority_column_names()
    bring = [c for c in priority if c in ss_existing.columns and c not in have_already]
    bring += [c for c in ss_new_cols if c not in bring]
    return bring


def _count_sofascore_matched(
    unified: pd.DataFrame, existing_leagues: set[str]
) -> int:
    if "sofascore_id" not in unified.columns:
        return 0
    mask = unified["league"].isin(existing_leagues)
    sid = pd.to_numeric(unified.loc[mask, "sofascore_id"], errors="coerce").fillna(0)
    return int((sid > 0).sum())


def _apply_name_norm_ss(unified: pd.DataFrame) -> None:
    """SofaScore lookup name: manual override per (norm name, league) when configured."""
    if "_name_norm" not in unified.columns:
        unified["_name_norm"] = unified["player"].apply(_norm_name)
    overrides = MANUAL_SS_OVERRIDES
    unified["_name_norm_ss"] = [
        overrides.get((str(n), str(lg)), str(n))
        for n, lg in zip(unified["_name_norm"], unified["league"])
    ]


def _assign_sofascore_row(
    unified: pd.DataFrame,
    row_idx: int,
    ss_row: pd.Series,
    bring: list[str],
) -> None:
    """Copy SofaScore stat columns onto one unified row (overwrite missing/zero id)."""
    for col in bring:
        if col not in ss_row.index:
            continue
        if col not in unified.columns:
            unified[col] = pd.NA
        val = ss_row[col]
        if col == "sofascore_id":
            old = pd.to_numeric(unified.at[row_idx, col], errors="coerce")
            if pd.notna(old) and old > 0:
                continue
        unified.at[row_idx, col] = val


def _reep_sofascore_backfill(
    unified: pd.DataFrame,
    ss_existing: pd.DataFrame,
    bring: list[str],
    bridge: dict[int, int],
    league_mask: pd.Series,
) -> int:
    """Match unmatched Big 5 rows via REEP understat_id -> sofascore_id."""
    if not bridge or "understat_id" not in unified.columns:
        return 0

    sid = pd.to_numeric(unified["sofascore_id"], errors="coerce").fillna(0)
    missing = league_mask & (sid <= 0)
    if not missing.any():
        return 0

    sort_col = (
        "sofascore_rating"
        if "sofascore_rating" in ss_existing.columns
        else "sofascore_id"
    )
    ss_dedup = (
        ss_existing.copy()
        .assign(
            sofascore_id=pd.to_numeric(ss_existing["sofascore_id"], errors="coerce"),
        )
        .dropna(subset=["sofascore_id"])
        .query("sofascore_id > 0")
        .sort_values(sort_col, ascending=False)
        .drop_duplicates(subset=["sofascore_id", "league", "season"], keep="first")
    )
    if ss_dedup.empty:
        return 0

    lookup = ss_dedup.set_index(["sofascore_id", "league", "season"])
    n_filled = 0
    for idx in unified.index[missing]:
        us_id = pd.to_numeric(unified.at[idx, "understat_id"], errors="coerce")
        if pd.isna(us_id) or int(us_id) <= 0:
            continue
        ss_id = bridge.get(int(us_id))
        if ss_id is None:
            continue
        key = (ss_id, unified.at[idx, "league"], unified.at[idx, "season"])
        if key not in lookup.index:
            continue
        ss_row = lookup.loc[key]
        if isinstance(ss_row, pd.DataFrame):
            ss_row = ss_row.iloc[0]
        _assign_sofascore_row(unified, idx, ss_row, bring)
        n_filled += 1
    return n_filled


def _fuzzy_sofascore_backfill(
    unified: pd.DataFrame,
    ss_existing: pd.DataFrame,
    bring: list[str],
    league_mask: pd.Series,
) -> int:
    """Fuzzy name match for rows still missing SofaScore stats (same league+season)."""
    from rapidfuzz import fuzz, process

    sid = pd.to_numeric(unified["sofascore_id"], errors="coerce").fillna(0)
    missing = league_mask & (sid <= 0)
    if not missing.any():
        return 0

    matched_ss_ids: set[int] = set()
    if "sofascore_id" in unified.columns:
        for v in pd.to_numeric(unified.loc[league_mask, "sofascore_id"], errors="coerce"):
            if pd.notna(v) and v > 0:
                matched_ss_ids.add(int(v))

    n_filled = 0
    for (league, season), u_group in unified.loc[missing].groupby(
        ["league", "season"], sort=False
    ):
        ss_bucket = ss_existing[
            (ss_existing["league"] == league) & (ss_existing["season"] == season)
        ].copy()
        if ss_bucket.empty:
            continue
        if "_team_norm" not in ss_bucket.columns and "team" in ss_bucket.columns:
            ss_bucket["_team_norm"] = ss_bucket["team"].apply(_norm_team)

        pool = ss_bucket[
            ~pd.to_numeric(ss_bucket["sofascore_id"], errors="coerce")
            .fillna(0)
            .astype(int)
            .isin(matched_ss_ids)
        ]
        if pool.empty:
            continue

        for u_idx, u_row in u_group.iterrows():
            u_name = str(u_row.get("_name_norm", "") or "")
            if not u_name:
                continue
            u_team = _norm_team(str(u_row.get("team", "") or ""))

            candidates = pool
            cutoff = _FUZZY_SS_PLAYER_ONLY_CUTOFF
            if u_team and "_team_norm" in candidates.columns:
                team_names = sorted(
                    {str(t) for t in candidates["_team_norm"].unique() if str(t)}
                )
                if team_names:
                    team_hit = process.extractOne(
                        u_team, team_names, score_cutoff=_FUZZY_SS_CLUB_CUTOFF
                    )
                    if team_hit is not None:
                        candidates = candidates[candidates["_team_norm"] == team_hit[0]]
                        cutoff = _FUZZY_SS_IN_CLUB_CUTOFF

            if len(u_name) <= 5:
                cutoff = max(cutoff, 95)

            ss_names = candidates["_name_norm"].astype(str).tolist()
            if not ss_names:
                continue
            hit = process.extractOne(
                u_name, ss_names, scorer=fuzz.WRatio, score_cutoff=cutoff
            )
            if hit is None:
                continue
            _best, _score, pos = hit
            ss_row = candidates.iloc[pos]
            ss_id = pd.to_numeric(ss_row.get("sofascore_id"), errors="coerce")
            if pd.isna(ss_id) or ss_id <= 0:
                continue
            _assign_sofascore_row(unified, u_idx, ss_row, bring)
            matched_ss_ids.add(int(ss_id))
            pool = pool.drop(ss_row.name, errors="ignore")
            n_filled += 1

    return n_filled


def _sofascore_columns_to_propagate(bring: list[str]) -> list[str]:
    """SofaScore fields copied when propagating a match across seasons."""
    cols = ["sofascore_id"]
    if "sofascore_team_id" in bring:
        cols.append("sofascore_team_id")
    cols.extend(c for c in bring if c not in cols)
    return cols


def _copy_sofascore_row(
    unified: pd.DataFrame,
    target_idx: pd.Index,
    source_idx: int,
    cols: list[str],
) -> None:
    """Write SofaScore columns from one unified row onto others."""
    for col in cols:
        if col not in unified.columns:
            continue
        unified.loc[target_idx, col] = unified.at[source_idx, col]


def _best_sofascore_source_index(
    unified: pd.DataFrame,
    indices: pd.Index,
    *,
    minutes_col: str = "minutes",
) -> int | None:
    """Pick the row with a SofaScore id and the most minutes in ``indices``."""
    if "sofascore_id" not in unified.columns:
        return None
    sid = pd.to_numeric(unified.loc[indices, "sofascore_id"], errors="coerce").fillna(0)
    hit = indices[sid > 0]
    if len(hit) == 0:
        return None
    if minutes_col in unified.columns:
        mins = pd.to_numeric(unified.loc[hit, minutes_col], errors="coerce").fillna(0)
        return int(mins.idxmax())
    return int(hit[0])


def _propagate_sofascore_cross_season(
    unified: pd.DataFrame,
    existing_leagues: set[str],
    bring: list[str],
) -> int:
    """
    Fill missing SofaScore stats from another season for the same player.

    1. Group by ``understat_id`` (stable on Understat across seasons).
    2. Then by ``(_name_norm, league)`` for any still missing.
    """
    if "sofascore_id" not in unified.columns:
        return 0

    league_mask = unified["league"].isin(existing_leagues)
    if not league_mask.any():
        return 0

    sid = pd.to_numeric(unified["sofascore_id"], errors="coerce").fillna(0)
    missing = league_mask & (sid <= 0)
    if not missing.any():
        return 0

    cols = _sofascore_columns_to_propagate(bring)
    n_filled = 0
    subset = unified.loc[league_mask]

    # Pass 1: same Understat player id
    if "understat_id" in unified.columns:
        us = pd.to_numeric(subset["understat_id"], errors="coerce")
        for uid in us[us > 0].unique():
            grp = subset.index[us == uid]
            src = _best_sofascore_source_index(unified, grp)
            if src is None:
                continue
            miss = grp[pd.to_numeric(unified.loc[grp, "sofascore_id"], errors="coerce").fillna(0) <= 0]
            if len(miss) == 0:
                continue
            _copy_sofascore_row(unified, miss, src, cols)
            n_filled += len(miss)

    # Pass 2: same normalised name + league (transfers keep league row per season)
    subset = unified.loc[league_mask]
    sid = pd.to_numeric(unified["sofascore_id"], errors="coerce").fillna(0)
    still = league_mask & (sid <= 0)
    if still.any() and "_name_norm" in subset.columns:
        sub2 = unified.loc[still, ["_name_norm", "league"]].copy()
        for (name, league), _ in sub2.groupby(["_name_norm", "league"], sort=False):
            grp = subset.index[
                (subset["_name_norm"] == name) & (subset["league"] == league)
            ]
            src = _best_sofascore_source_index(unified, grp)
            if src is None:
                continue
            miss = grp[pd.to_numeric(unified.loc[grp, "sofascore_id"], errors="coerce").fillna(0) <= 0]
            if len(miss) == 0:
                continue
            _copy_sofascore_row(unified, miss, src, cols)
            n_filled += len(miss)

    return n_filled


def _log_sofascore_unresolved(
    unified: pd.DataFrame, existing_leagues: set[str]
) -> None:
    """Log per-league counts of rows that still lack a SofaScore id."""
    if "sofascore_id" not in unified.columns:
        return
    in_big5 = unified["league"].isin(existing_leagues)
    sid = pd.to_numeric(unified["sofascore_id"], errors="coerce").fillna(0)
    failed = in_big5 & (sid <= 0)
    if not failed.any():
        return
    by_league = unified.loc[failed].groupby("league").size().sort_values(ascending=False)
    parts = [f"{lg}: {cnt}" for lg, cnt in by_league.items()]
    log.info("    SofaScore still unresolved (Big 5): %s — %s", int(failed.sum()), ", ".join(parts))


def _merge_sofascore_into(unified: pd.DataFrame, ss_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge SofaScore stats into the unified DataFrame.

    Strategy:
    - For leagues already in unified (Big5 etc.): merge in NEW columns only.
      Understat xG/goals/assists/minutes are kept; don't overwrite them.
      Unmatched names: manual aliases, REEP id bridge, then fuzzy name+club.
    - For leagues NOT in unified (Eredivisie, UCL, etc.): add new rows from SofaScore.
    """
    if ss_df.empty:
        return unified

    log.info(f"  Merging SofaScore: {len(ss_df)} records, {ss_df['league'].nunique()} leagues")

    if "_name_norm" not in ss_df.columns:
        ss_df = ss_df.copy()
        ss_df["_name_norm"] = ss_df["player"].apply(_norm_name)

    existing_leagues = set(unified["league"].unique())
    ss_existing = ss_df[ss_df["league"].isin(existing_leagues)].copy()
    ss_new      = ss_df[~ss_df["league"].isin(existing_leagues)].copy()

    merge_key = ["_name_norm_ss", "league", "season"]

    # ── Part 1: merge new columns into existing rows ──────────────────────────
    if not ss_existing.empty:
        unified["_name_norm"] = unified["player"].apply(_norm_name)
        _apply_name_norm_ss(unified)
        league_mask = unified["league"].isin(existing_leagues)
        n_big5 = int(league_mask.sum())

        bring = _sofascore_bring_columns(unified, ss_existing)
        n_before = _count_sofascore_matched(unified, existing_leagues)

        if bring:
            sort_col = (
                "sofascore_rating"
                if "sofascore_rating" in ss_existing.columns
                else bring[0]
            )
            ss_merge = (
                ss_existing[["_name_norm", "league", "season"] + bring]
                .rename(columns={"_name_norm": "_name_norm_ss"})
                .sort_values(sort_col, ascending=False)
                .drop_duplicates(subset=merge_key, keep="first")
            )
            # Drop placeholder cols so pandas does not create _x / _y suffixes.
            left = unified.drop(
                columns=[c for c in bring if c in unified.columns],
                errors="ignore",
            )
            unified = left.merge(ss_merge, on=merge_key, how="left")
            # merge resets RangeIndex; refresh mask for in-place backfills
            league_mask = unified["league"].isin(existing_leagues)

        n_after_exact = _count_sofascore_matched(unified, existing_leagues)
        n_exact = n_after_exact - n_before

        bridge = _load_reep_understat_to_sofascore()
        n_reep = _reep_sofascore_backfill(
            unified, ss_existing, bring, bridge, league_mask
        )

        n_fuzzy = _fuzzy_sofascore_backfill(
            unified, ss_existing, bring, league_mask
        )

        n_propagated = _propagate_sofascore_cross_season(
            unified, existing_leagues, bring
        )

        n_final = _count_sofascore_matched(unified, existing_leagues)
        n_unresolved = n_big5 - n_final

        if "sofascore_id" in unified.columns:
            sid = pd.to_numeric(unified["sofascore_id"], errors="coerce").fillna(0)
            unified["_ss_merge_failed"] = league_mask & (sid <= 0)
        else:
            unified["_ss_merge_failed"] = league_mask

        log.info(
            "    SofaScore Big 5: %s/%s matched "
            "(%s exact name, %s REEP id, %s fuzzy, %s cross-season, %s unresolved)",
            n_final,
            n_big5,
            n_exact,
            n_reep,
            n_fuzzy,
            n_propagated,
            n_unresolved,
        )
        if n_unresolved > 0:
            _log_sofascore_unresolved(unified, existing_leagues)

        unified = unified.drop(
            columns=["_name_norm", "_name_norm_ss"], errors="ignore"
        )

    # ── Part 2: add rows for new leagues (Eredivisie, UCL, etc.) ─────────────
    if not ss_new.empty:
        new_leagues = sorted(ss_new["league"].unique())
        log.info(f"  Adding {len(new_leagues)} new leagues from SofaScore: {new_leagues}")
        new_rows = _build_base_from_sofascore(ss_new)
        # Ensure no duplicate columns before concat
        new_rows = new_rows.loc[:, ~new_rows.columns.duplicated(keep="first")]
        unified  = unified.loc[:, ~unified.columns.duplicated(keep="first")]
        unified  = pd.concat([unified, new_rows], ignore_index=True)
        log.info(f"  Unified now {len(unified)} rows after adding SofaScore leagues")

    if "_ss_merge_failed" in unified.columns:
        unified["_ss_merge_failed"] = unified["_ss_merge_failed"].fillna(False).astype(bool)

    return unified


# Columns recomputed from summed parts on season-total rows (not summed directly).
_PCT_FROM_PARTS: list[tuple[str, str, str]] = [
    ("pass_completion_pct", "passes_completed", "passes_total"),
    ("dribbles_pct", "dribbles_completed", "dribbles_attempted"),
    ("long_balls_pct", "long_balls_completed", "long_balls_total"),
    ("crosses_pct", "crosses_completed", "crosses_total"),
    ("tackles_won_pct", "tackles_won", "tackles"),
    ("passes_opp_half_pct", "passes_opp_half", "passes_opp_half_total"),
    ("passes_own_half_pct", "passes_own_half", "passes_own_half_total"),
    ("chipped_passes_pct", "chipped_passes_completed", "chipped_passes_total"),
    (
        "shots_inside_box_conversion_pct",
        "goals_inside_box",
        "shots_inside_box",
    ),
    (
        "shots_outside_box_conversion_pct",
        "goals_outside_box",
        "shots_outside_box",
    ),
]

_SEASON_TOTAL_SKIP_SUM: frozenset[str] = frozenset({
    "league",
    "season",
    "player",
    "team",
    "is_season_total",
    "_ss_merge_failed",
    "xg_overperformance",
    "npxg_overperformance",
    "sofascore_rating",
    "understat_id",
    "sofascore_id",
    "sofascore_team_id",
    "xg_source",
    "xag_source",
})

# Rate/percentage columns recomputed after volume sums (never summed across competitions).
_SEASON_TOTAL_RECOMPUTE_PCT: frozenset[str] = frozenset({
    "set_piece_conversion",
})


def _apply_player_identity_columns(unified: pd.DataFrame) -> None:
    """Set canonical ``player`` strings and ``_name_norm`` on every row (in-place)."""
    if "player" not in unified.columns:
        return
    unified["player"] = unified["player"].map(sanitize_player_name)
    unified["_name_norm"] = unified["player"].map(_norm_name)


def _competition_rows_mask(unified: pd.DataFrame) -> pd.Series:
    """Rows that represent a single competition (not an existing season total)."""
    if "is_season_total" in unified.columns:
        not_total = ~unified["is_season_total"].fillna(False).astype(bool)
    else:
        not_total = pd.Series(True, index=unified.index)
    if "league" in unified.columns:
        not_total &= unified["league"] != SEASON_TOTAL_LEAGUE
    return not_total


def _ground_duels_lost_from_won_pct(won: float, pct: float) -> float:
    """Infer ground duels lost from SofaScore won count and win percentage."""
    if won <= 0 or pct <= 0:
        return 0.0
    if pct >= 100:
        return 0.0
    return won * (100.0 / pct - 1.0)


def _recompute_pct_columns(totals: dict[str, float], grp: pd.DataFrame | None = None) -> None:
    """Fill percentage columns from summed numerators/denominators (in-place)."""
    for pct_col, num_col, den_col in _PCT_FROM_PARTS:
        if pct_col not in totals:
            continue
        num = totals.get(num_col, 0) or 0
        den = totals.get(den_col, 0) or 0
        if den > 0:
            totals[pct_col] = round(100.0 * num / den, 2)
        else:
            totals[pct_col] = 0.0

    if "aerials_won_pct" in totals or (
        "aerials_won" in totals and "aerials_lost" in totals
    ):
        won = float(totals.get("aerials_won", 0) or 0)
        lost = float(totals.get("aerials_lost", 0) or 0)
        aerial_den = won + lost
        totals["aerials_won_pct"] = (
            round(100.0 * won / aerial_den, 1) if aerial_den > 0 else 0.0
        )

    if "duels_won_pct" in totals and "duels_won" in totals and "duels_lost" in totals:
        won = float(totals.get("duels_won", 0) or 0)
        lost = float(totals.get("duels_lost", 0) or 0)
        duel_den = won + lost
        totals["duels_won_pct"] = (
            round(100.0 * won / duel_den, 1) if duel_den > 0 else 0.0
        )

    if grp is not None and "ground_duels_won_pct" in totals:
        total_won = 0.0
        total_lost = 0.0
        for _, row in grp.iterrows():
            won = float(pd.to_numeric(row.get("ground_duels_won"), errors="coerce") or 0)
            pct = float(pd.to_numeric(row.get("ground_duels_won_pct"), errors="coerce") or 0)
            if won <= 0:
                continue
            total_won += won
            total_lost += _ground_duels_lost_from_won_pct(won, pct)
        den = total_won + total_lost
        totals["ground_duels_won_pct"] = (
            round(100.0 * total_won / den, 2) if den > 0 else 0.0
        )

    if "goal_conversion_pct" in totals:
        goals = float(totals.get("goals", 0) or 0)
        shots = float(totals.get("shots", 0) or 0)
        totals["goal_conversion_pct"] = (
            round(100.0 * goals / shots, 2) if shots > 0 else 0.0
        )

    if "set_piece_conversion" in totals:
        fk_goals = float(totals.get("goals_freekick", 0) or 0)
        sp_shots = float(totals.get("shots_set_piece", 0) or 0)
        totals["set_piece_conversion"] = (
            round(100.0 * fk_goals / sp_shots, 2) if sp_shots > 0 else 0.0
        )

    if "pen_conversion_pct" in totals:
        pen_goals = float(totals.get("goals_penalty", 0) or 0)
        pens_taken = float(totals.get("pens_taken", 0) or 0)
        totals["pen_conversion_pct"] = (
            round(100.0 * pen_goals / pens_taken, 2) if pens_taken > 0 else 0.0
        )

    rt = float(totals.get("sofascore_rating_total", 0) or 0)
    rc = float(totals.get("sofascore_rating_count", 0) or 0)
    if rc > 0 and "sofascore_rating" in totals:
        totals["sofascore_rating"] = round(rt / rc, 2)


def _aggregate_season_total_group(grp: pd.DataFrame) -> dict:
    """Build one All Competitions row from competition rows in a player-season group."""
    minutes = pd.to_numeric(grp["minutes"], errors="coerce").fillna(0)
    primary_idx = minutes.idxmax()
    primary = grp.loc[primary_idx]

    us_mask = pd.to_numeric(grp.get("understat_id"), errors="coerce").fillna(0) > 0
    if us_mask.any():
        us_primary_idx = minutes[us_mask].idxmax()
        us_primary = grp.loc[us_primary_idx]
    else:
        us_primary = primary

    totals: dict = primary.to_dict()

    numeric_cols = grp.select_dtypes(include="number").columns
    for col in numeric_cols:
        if col in _SEASON_TOTAL_SKIP_SUM:
            continue
        if col.endswith("_per90") or col.endswith("_pct") or col in _SEASON_TOTAL_RECOMPUTE_PCT:
            continue
        if col.endswith("_id") or col in ("tm_id", "market_value_eur"):
            continue
        if col.startswith("eafc_"):
            continue
        totals[col] = pd.to_numeric(grp[col], errors="coerce").sum(min_count=1)
        if pd.isna(totals[col]):
            totals[col] = 0

    # Minutes-weighted SofaScore rating when count columns are missing/zero.
    if float(totals.get("sofascore_rating_count", 0) or 0) <= 0:
        ratings = pd.to_numeric(grp.get("sofascore_rating"), errors="coerce")
        mins = minutes.astype(float)
        mask = (ratings > 0) & (mins > 0)
        if mask.any():
            totals["sofascore_rating"] = round(
                (ratings[mask] * mins[mask]).sum() / mins[mask].sum(), 2
            )

    _recompute_pct_columns(totals, grp)

    teams = (
        grp["team"].dropna().astype(str).str.strip().replace("", pd.NA).dropna().unique()
    )
    totals["team"] = ",".join(sorted(set(teams))) if len(teams) else str(primary.get("team", ""))
    totals["league"] = SEASON_TOTAL_LEAGUE
    totals["season"] = primary["season"]
    totals["player"] = sanitize_player_name(str(primary.get("player", "")))
    if "_name_norm" in grp.columns and pd.notna(primary.get("_name_norm")):
        totals["_name_norm"] = primary["_name_norm"]
    totals["is_season_total"] = True
    totals["sofascore_id"] = primary.get("sofascore_id")
    totals["understat_id"] = us_primary.get("understat_id")
    if "sofascore_team_id" in grp.columns:
        totals["sofascore_team_id"] = primary.get("sofascore_team_id")

    if "_ss_merge_failed" in grp.columns:
        totals["_ss_merge_failed"] = bool(grp["_ss_merge_failed"].fillna(False).any())

    return totals


def _assign_season_total_group_key(comp: pd.DataFrame) -> pd.Series:
    """
    Group key for season totals: ``(sofascore_id, season)`` when id > 0, else
    ``(_name_norm, season)`` for rows without a SofaScore id.
    """
    sid = pd.to_numeric(comp["sofascore_id"], errors="coerce").fillna(0)
    has_ss = sid > 0
    key = pd.Series(index=comp.index, dtype="string")
    key.loc[has_ss] = "ss:" + sid.loc[has_ss].astype("int64").astype(str) + ":" + comp.loc[has_ss, "season"].astype(str)
    no_ss = ~has_ss
    if no_ss.any():
        key.loc[no_ss] = (
            "nm:" + comp.loc[no_ss, "_name_norm"].astype(str) + ":" + comp.loc[no_ss, "season"].astype(str)
        )
    return key


def _append_season_total_rows(unified: pd.DataFrame) -> pd.DataFrame:
    """
    Add one ``All Competitions`` row per player-season that has 2+ competition rows.

    Competition rows are unchanged. Grouping is by ``(sofascore_id, season)`` when
    ``sofascore_id > 0``, otherwise ``(_name_norm, season)``.
    """
    if unified.empty or "season" not in unified.columns:
        return unified

    unified = unified.copy()
    if "is_season_total" not in unified.columns:
        unified["is_season_total"] = False
    else:
        unified["is_season_total"] = (
            unified["is_season_total"].fillna(False).astype(bool)
        )

    comp_mask = _competition_rows_mask(unified)
    comp = unified.loc[comp_mask].copy()
    if comp.empty:
        unified["is_season_total"] = unified["is_season_total"].fillna(False)
        return unified

    _apply_player_identity_columns(comp)
    comp["is_season_total"] = False

    comp["_st_group_key"] = _assign_season_total_group_key(comp)

    multi_keys: list[str] = []
    multi_name_norm_in_ss_group = 0
    total_rows: list[dict] = []

    for group_key, grp in comp.groupby("_st_group_key", sort=False):
        if len(grp) < 2:
            continue
        multi_keys.append(str(group_key))
        if str(group_key).startswith("ss:"):
            norms = grp["_name_norm"].dropna().unique()
            if len(norms) > 1:
                multi_name_norm_in_ss_group += 1
        primary_norm = grp.loc[
            pd.to_numeric(grp["minutes"], errors="coerce").fillna(0).idxmax(),
            "_name_norm",
        ]
        row = _aggregate_season_total_group(grp)
        row["_name_norm"] = primary_norm
        total_rows.append(row)

    if not total_rows:
        return comp.drop(columns=["_st_group_key"], errors="ignore")

    totals_df = pd.DataFrame(total_rows)
    for col in comp.columns:
        if col not in totals_df.columns:
            totals_df[col] = pd.NA

    out_cols = [c for c in comp.columns if c != "_st_group_key"]
    unified = pd.concat([comp[out_cols], totals_df[out_cols]], ignore_index=True)

    log.info(
        "  Season totals: added %s All Competitions rows (%s sofascore_id/name groups with 2+ competitions)",
        len(total_rows),
        len(multi_keys),
    )
    if multi_name_norm_in_ss_group:
        log.debug(
            "  Season totals: %s sofascore_id groups had multiple display names (e.g. accent variants)",
            multi_name_norm_in_ss_group,
        )

    return unified


def _compute_derived_pct_columns(unified: pd.DataFrame) -> None:
    """Fill ratio/percentage columns from summed volume parts (in-place)."""
    for pct_col, num_col, den_col in _PCT_FROM_PARTS:
        if pct_col in (
            "pass_completion_pct",
            "dribbles_pct",
            "long_balls_pct",
            "crosses_pct",
            "tackles_won_pct",
        ):
            continue
        if num_col not in unified.columns or den_col not in unified.columns:
            continue
        num = pd.to_numeric(unified[num_col], errors="coerce")
        den = pd.to_numeric(unified[den_col], errors="coerce").replace(0, pd.NA)
        pct = 100.0 * num / den
        unified[pct_col] = pd.to_numeric(pct, errors="coerce").round(2).fillna(0)


def _backfill_non_big5_xg_from_sofascore(
    unified: pd.DataFrame, ss_df: pd.DataFrame
) -> pd.DataFrame:
    """
    For non–Big 5 rows, copy SofaScore season xG/xAG when unified has 0 but raw SS > 0.
    """
    if ss_df.empty or unified.empty:
        return unified

    big5 = set(UNDERSTAT_LEAGUES.keys())
    if "league" not in unified.columns:
        return unified

    mask = ~unified["league"].isin(big5) & (unified["league"] != SEASON_TOTAL_LEAGUE)
    if not mask.any():
        return unified

    ss = ss_df.copy()
    ss["sofascore_id"] = pd.to_numeric(ss.get("sofascore_id"), errors="coerce")
    keys = ["league", "season", "sofascore_id"]

    sub = unified.loc[mask].copy()
    sub["sofascore_id"] = pd.to_numeric(sub["sofascore_id"], errors="coerce")

    for col in ("xg", "xag"):
        if col not in ss.columns:
            continue
        lookup = (
            ss[keys + [col]]
            .dropna(subset=["sofascore_id"])
            .query("sofascore_id > 0")
            .rename(columns={col: f"{col}_ss"})
            .drop_duplicates(subset=keys, keep="first")
        )
        if lookup.empty:
            continue
        merged = sub[keys].merge(lookup, on=keys, how="left")
        cur = pd.to_numeric(sub[col], errors="coerce").fillna(0).to_numpy()
        ss_vals = pd.to_numeric(merged[f"{col}_ss"], errors="coerce").fillna(0).to_numpy()
        need = (cur <= 0) & (ss_vals > 0)
        n = int(need.sum())
        if n:
            unified.loc[sub.index[need], col] = ss_vals[need]
            log.info("  Backfilled %s from SofaScore raw for %d non–Big 5 rows", col, n)

    return unified


def _apply_xg_source_columns(unified: pd.DataFrame) -> None:
    """Tag ``xg_source`` / ``xag_source`` per row (in-place)."""
    big5 = set(UNDERSTAT_LEAGUES.keys())
    league = unified["league"].astype(str)

    us_id = pd.to_numeric(unified.get("understat_id"), errors="coerce")
    sid = pd.to_numeric(unified.get("sofascore_id"), errors="coerce")
    has_us = us_id.notna() & (us_id > 0)
    has_ss = sid.notna() & (sid > 0)

    xg = pd.to_numeric(unified.get("xg"), errors="coerce")
    xag = pd.to_numeric(unified.get("xag"), errors="coerce")

    unified["xg_source"] = pd.NA
    unified["xag_source"] = pd.NA

    us_league = league.isin(big5) & has_us
    unified.loc[us_league, "xg_source"] = XG_SOURCE_UNDERSTAT
    unified.loc[us_league, "xag_source"] = XG_SOURCE_UNDERSTAT

    # Multi-comp season totals tied to a domestic Understat id
    st_us = (league == SEASON_TOTAL_LEAGUE) & has_us
    unified.loc[st_us, "xg_source"] = XG_SOURCE_UNDERSTAT
    unified.loc[st_us, "xag_source"] = XG_SOURCE_UNDERSTAT

    ss_xg = has_ss & xg.notna() & unified["xg_source"].isna()
    unified.loc[ss_xg, "xg_source"] = XG_SOURCE_SOFASCORE

    ss_xag = has_ss & xag.notna() & unified["xag_source"].isna()
    unified.loc[ss_xag, "xag_source"] = XG_SOURCE_SOFASCORE


def _finalize_and_save(
    unified: pd.DataFrame, *, export_csv: bool = False
) -> pd.DataFrame:
    """Compute derived columns, fill NaN, save Parquet (+ optional CSV) + manifest."""
    # Defragment before adding many new columns (avoids PerformanceWarning)
    unified = unified.copy()

    _apply_player_identity_columns(unified)
    _compute_derived_pct_columns(unified)
    _apply_xg_source_columns(unified)

    # Numeric fill
    numeric_cols = unified.select_dtypes(include="number").columns
    unified[numeric_cols] = unified[numeric_cols].fillna(0)

    # ninety_s — always derive from minutes (fillna(0) can leave bogus zeros on SofaScore rows)
    if "minutes" in unified.columns:
        minutes = pd.to_numeric(unified["minutes"], errors="coerce")
        unified["ninety_s"] = (minutes / 90).round(2)

    n90 = unified["ninety_s"].replace(0, float("nan"))

    per90_pairs = [
        ("goals",               "goals_per90"),
        ("assists",             "assists_per90"),
        ("npg",                 "npg_per90"),
        ("xg",                  "xg_per90"),
        ("xag",                 "xag_per90"),
        ("npxg",                "npxg_per90"),
        ("xg_chain",            "xg_chain_per90"),
        ("xg_buildup",          "xg_buildup_per90"),
        ("shots",               "shots_per90"),
        ("key_passes",          "key_passes_per90"),
        ("tackles_won",         "tackles_won_per90"),
        ("interceptions",       "interceptions_per90"),
        ("big_chances_created", "big_chances_created_per90"),
        ("dribbles_completed",  "dribbles_per90"),
        ("progressive_passes",  "prog_passes_per90"),
        ("progressive_carries", "prog_carries_per90"),
        ("sca",                 "sca_per90"),
        ("gca",                 "gca_per90"),
    ]
    for src, dst in per90_pairs:
        if src in unified.columns and dst not in unified.columns:
            unified[dst] = (pd.to_numeric(unified[src], errors="coerce") / n90).round(3).fillna(0)

    # Aerial %
    if "aerials_won" in unified.columns and "aerials_lost" in unified.columns:
        if "aerials_won_pct" not in unified.columns or (unified["aerials_won_pct"] == 0).all():
            tot = unified["aerials_won"] + unified["aerials_lost"]
            unified["aerials_won_pct"] = (
                unified["aerials_won"] / tot.replace(0, float("nan")) * 100
            ).round(1).fillna(0)

    # xG overperformance
    if "goals" in unified.columns and "xg" in unified.columns:
        unified["xg_overperformance"] = (
            pd.to_numeric(unified["goals"], errors="coerce") -
            pd.to_numeric(unified["xg"],   errors="coerce")
        ).round(2).fillna(0)

    if "npg" in unified.columns and "npxg" in unified.columns:
        unified["npxg_overperformance"] = (
            pd.to_numeric(unified["npg"],  errors="coerce") -
            pd.to_numeric(unified["npxg"], errors="coerce")
        ).round(2).fillna(0)

    # Sort
    sort_cols = [c for c in ["league","season","team","player"] if c in unified.columns]
    if sort_cols:
        unified = unified.sort_values(sort_cols).reset_index(drop=True)

    # Save primary artifact as Parquet (columnar, typed, compact).
    be = get_backend()
    out_pq = be.write_parquet_rel("unified_player_stats.parquet", unified)
    log.info(
        f"✅ Unified Parquet: {len(unified)} rows × {len(unified.columns)} cols → {out_pq}"
    )
    if export_csv:
        out_csv = be.write_csv_rel("unified_player_stats.csv", unified)
        log.info(f"  (also wrote CSV for spreadsheet use → {out_csv})")

    manifest = {
        "columns":       list(unified.columns),
        "leagues":       sorted(unified["league"].unique().tolist()) if "league" in unified.columns else [],
        "seasons":       sorted(unified["season"].unique().tolist()) if "season" in unified.columns else [],
        "row_count":     len(unified),
        "unified_path":  str(out_pq),
        "xg_available":  bool("xg" in unified.columns and (unified["xg"] > 0).any()),
        "rating_available": bool("sofascore_rating" in unified.columns and
                                 (unified["sofascore_rating"] > 0).any()),
        "last_built_at": datetime.now(timezone.utc).isoformat(),
    }
    oldest = None
    if be.exists_rel("raw/.freshness.json"):
        try:
            fr = be.read_json_rel("raw/.freshness.json")
            for _k, meta in fr.items():
                ts = meta.get("fetched_at")
                if not ts:
                    continue
                try:
                    # fromisoformat accepts +00:00
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                oldest = t if oldest is None or t < oldest else oldest
        except Exception as e:
            log.warning(f"Could not read freshness for manifest: {e}")
    manifest["oldest_source_fetched_at"] = oldest.isoformat() if oldest else None
    be.write_json_rel("manifest.json", manifest)
    log.info("📋 Manifest saved")
    return unified


# ══════════════════════════════════════════════════════════════════════════════
#  Main build
# ══════════════════════════════════════════════════════════════════════════════

def build_unified(export_csv: bool = False):
    """
    Merge all raw data into data/unified_player_stats.parquet.

    Layer order (each adds/overrides):
      1. Understat — season xG/xA and core stats for Big 5 leagues (base when present)
      2. SofaScore — 80+ stats; merges into Big 5 rows; adds rows for other leagues
      3. Transfermarkt + Capology — market value, wages, contract (via merge_financial_data)
      4. EA FC — physical/technical attributes, traits, work rates (via merge_eafc_data)
      5. Season totals — one ``All Competitions`` row when a player has 2+ leagues/season

    Leagues without Understat use a SofaScore-only base.
    """
    log.info("🔨 Building unified player stats …")

    us_df = load_all_understat_raw()
    ss_df = load_all_sofascore_raw()

    if us_df.empty and ss_df.empty:
        log.error("No raw data found. Run: python3 -m collect_data")
        return pd.DataFrame()

    # ── Layer 1: base (Understat for Big 5, else SofaScore) ─────────────────
    if not us_df.empty:
        log.info("  Base from Understat (Big 5)")
        unified = _build_base_from_understat(us_df)
        us_df = pd.DataFrame()  # xG already in base
    else:
        log.info("  Base from SofaScore only (no Understat raw)")
        unified = _build_base_from_sofascore(ss_df)
        ss_df = pd.DataFrame()  # already consumed

    # ── Layer 2: Understat xG (SofaScore-only base, e.g. partial Big 5) ─────
    if not us_df.empty:
        unified = _merge_understat_into(unified, us_df)

    # ── Layer 3: SofaScore ───────────────────────────────────────────────────
    if not ss_df.empty:
        unified = _merge_sofascore_into(unified, ss_df)

    # ── Layer 4: Financial data (Transfermarkt + Capology) ───────────────────
    unified = merge_financial_data(unified)

    # ── Layer 5: EA FC player attributes ─────────────────────────────────────
    unified = merge_eafc_data(unified)

    # ── Layer 6: per-player season totals (2+ competitions only) ─────────────
    unified = _append_season_total_rows(unified)

    ss_for_backfill = load_all_sofascore_raw()
    unified = _backfill_non_big5_xg_from_sofascore(unified, ss_for_backfill)

    result = _finalize_and_save(unified, export_csv=export_csv)
    try:
        from soccer_server import db

        db.refresh()
        log.info("DuckDB views refreshed after unified build")
    except Exception as e:
        log.warning("Could not refresh DuckDB views after build: %s", e)
    return result
