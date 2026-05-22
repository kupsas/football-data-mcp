"""Build ``unified_player_stats`` from raw parquet layers (Understat, SofaScore, financials)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pandas as pd

from collect_data.config import UNDERSTAT_AUTHORITATIVE
from collect_data.helpers import _norm_name
from collect_data.build.eafc import merge_eafc_data
from collect_data.build.financials import merge_financial_data
from collect_data.storage import get_backend, load_parquets

log = logging.getLogger(__name__)

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


def _merge_sofascore_into(unified: pd.DataFrame, ss_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge SofaScore stats into the unified DataFrame.

    Strategy:
    - For leagues already in unified (Big5 etc.): merge in NEW columns only.
      Understat xG/goals/assists/minutes are kept; don't overwrite them.
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

    merge_key = ["_name_norm", "league", "season"]

    # ── Part 1: merge new columns into existing rows ──────────────────────────
    if not ss_existing.empty:
        unified["_name_norm"] = unified["player"].apply(_norm_name)

        # Which columns do we already have (keep them, don't overwrite)?
        have_already = set(unified.columns) | UNDERSTAT_AUTHORITATIVE
        ss_new_cols  = [c for c in ss_existing.columns
                        if c not in have_already and c not in merge_key]

        # Always bring the rating + defensive/aerial stats regardless
        priority = [
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
            "blocked_shots", "shots_set_piece",
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
        bring = [c for c in priority if c in ss_existing.columns
                 and c not in have_already]
        # Also grab any other new columns we haven't listed
        bring += [c for c in ss_new_cols if c not in bring]

        if bring:
            ss_merge = (
                ss_existing[merge_key + bring]
                .sort_values("sofascore_rating" if "sofascore_rating" in ss_existing.columns
                             else bring[0], ascending=False)
                .drop_duplicates(subset=merge_key, keep="first")
            )
            unified = unified.merge(ss_merge, on=merge_key, how="left")

        unified = unified.drop(columns=["_name_norm"], errors="ignore")

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

    return unified


def _finalize_and_save(
    unified: pd.DataFrame, *, export_csv: bool = False
) -> pd.DataFrame:
    """Compute derived columns, fill NaN, save Parquet (+ optional CSV) + manifest."""
    # Defragment before adding many new columns (avoids PerformanceWarning)
    unified = unified.copy()

    # Numeric fill
    numeric_cols = unified.select_dtypes(include="number").columns
    unified[numeric_cols] = unified[numeric_cols].fillna(0)

    # ninety_s
    if "ninety_s" not in unified.columns and "minutes" in unified.columns:
        unified["ninety_s"] = (unified["minutes"] / 90).round(2)

    n90 = unified.get("ninety_s", pd.Series(dtype=float)).replace(0, float("nan"))

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

    result = _finalize_and_save(unified, export_csv=export_csv)
    try:
        from soccer_server import db

        db.refresh()
        log.info("DuckDB views refreshed after unified build")
    except Exception as e:
        log.warning("Could not refresh DuckDB views after build: %s", e)
    return result
