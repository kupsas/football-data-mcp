"""Merge Transfermarkt + Capology financial columns into the unified frame."""

from __future__ import annotations

import logging

import pandas as pd

from collect_data.config import MANUAL_TM_OVERRIDES
from collect_data.helpers import _norm_name
from collect_data.storage import RAW_DIR

log = logging.getLogger(__name__)


def _fuzzy_tm_fill(unmatched: pd.DataFrame, tm_lookup: pd.DataFrame,
                   tm_cols: list[str], threshold: int = 85) -> pd.DataFrame:
    """
    For rows where the exact name merge missed, try rapidfuzz WRatio within
    the same league+season bucket.  Only accepts matches >= threshold (0-100).
    Short names (<= 5 chars) require >= 95 to avoid false positives like
    'Kepa' matching 'Kepa Arrizabalaga' but not 'Igor Thiago' matching 'Thiago'.
    Returns a DataFrame with the same index as unmatched, filled where possible.
    """
    from rapidfuzz import process, fuzz

    filled = unmatched.copy()

    for (league, season), group in unmatched.groupby(["league", "season"]):
        bucket = tm_lookup[(tm_lookup["league"] == league) &
                           (tm_lookup["season"] == season)]
        if bucket.empty:
            continue
        tm_names  = bucket["_name_norm"].tolist()
        tm_rows   = bucket.set_index("_name_norm")

        for idx, row in group.iterrows():
            query = row["_name_norm"]
            cutoff = 95 if len(query) <= 5 else threshold
            result = process.extractOne(
                query, tm_names,
                scorer=fuzz.WRatio,
                score_cutoff=cutoff,
            )
            if result is None:
                continue
            best_name, score, _ = result
            for col in tm_cols:
                if col in tm_rows.columns:
                    filled.at[idx, col] = tm_rows.at[best_name, col]

    return filled


def merge_financial_data(unified: pd.DataFrame) -> pd.DataFrame:
    """Add market value, contract, wages to unified DataFrame from TM + Capology."""
    unified = unified.copy()  # defragment before adding columns
    unified["_name_norm"] = unified["player"].apply(_norm_name)

    # ── Transfermarkt ─────────────────────────────────────────────────────────
    # Pass 1: exact name match on name+league+season
    # Pass 2: rapidfuzz WRatio fuzzy match for what's still unmatched
    tm_files = [f for f in sorted(RAW_DIR.glob("transfermarkt__*.parquet"))
                if "mv_history" not in f.name and "transfers" not in f.name]
    if tm_files:
        tm_frames = []
        for f in tm_files:
            try:
                tm_frames.append(pd.read_parquet(f))
            except Exception as e:
                log.warning(f"Could not load {f.name}: {e}")
        if tm_frames:
            tm_df = pd.concat(tm_frames, ignore_index=True)
            if "_name_norm" not in tm_df.columns:
                tm_df["_name_norm"] = tm_df["tm_name"].apply(_norm_name)
            tm_cols = [c for c in
                       ["tm_id", "market_value_eur", "contract_expiration",
                        "height_m", "nationality", "citizenship", "tm_position"]
                       if c in tm_df.columns]
            tm_lookup = (tm_df[["_name_norm", "league", "season"] + tm_cols]
                         .sort_values("season", ascending=False)
                         .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first"))

            # Pass 0 — manual overrides (remap unified name → TM name before merge)
            for (nn, league_name), tm_nn in MANUAL_TM_OVERRIDES.items():
                mask = (unified["_name_norm"] == nn) & (unified["league"] == league_name)
                if mask.any():
                    unified.loc[mask, "_name_norm"] = tm_nn

            # Pass 1 — exact
            unified = unified.merge(tm_lookup, on=["_name_norm", "league", "season"], how="left")
            exact_matched = unified["tm_id"].notna().sum() if "tm_id" in unified.columns else 0

            # Pass 2 — fuzzy for remaining unmatched rows
            unmatched_mask = unified["tm_id"].isna()
            if unmatched_mask.any():
                filled = _fuzzy_tm_fill(
                    unified[unmatched_mask][["_name_norm", "league", "season"] + tm_cols],
                    tm_lookup, tm_cols,
                )
                for col in tm_cols:
                    if col in filled.columns:
                        unified.loc[unmatched_mask, col] = filled[col].values

            total_matched = unified["tm_id"].notna().sum() if "tm_id" in unified.columns else 0
            fuzzy_matched = total_matched - exact_matched
            log.info(f"  TM merge: {total_matched}/{len(unified)} matched "
                     f"({exact_matched} exact + {fuzzy_matched} fuzzy)")

    # ── Capology ──────────────────────────────────────────────────────────────
    cap_files = sorted(RAW_DIR.glob("capology__*.parquet"))
    if cap_files:
        cap_frames = []
        for f in cap_files:
            try:
                cap_frames.append(pd.read_parquet(f))
            except Exception as e:
                log.warning(f"Could not load {f.name}: {e}")
        if cap_frames:
            cap_df = pd.concat(cap_frames, ignore_index=True)
            # Find player name column (first non-league/season text col)
            name_col = next(
                (c for c in cap_df.columns
                 if c not in {"league", "season", "currency"}
                 and cap_df[c].dtype == object),
                None,
            )
            if name_col:
                cap_df["_name_norm"] = cap_df[name_col].apply(_norm_name)
                wage_cols = [c for c in cap_df.columns if any(
                    kw in c.lower() for kw in ["weekly", "annual", "gross", "wage", "salary"]
                )]
                log.info(f"  Capology wage columns: {wage_cols}")
                if wage_cols:
                    cap_merge = (
                        cap_df[["_name_norm", "league", "season"] + wage_cols]
                        .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first")
                    )
                    unified = unified.merge(
                        cap_merge, on=["_name_norm", "league", "season"], how="left"
                    )
                    log.info(f"  Capology merged wage columns: {wage_cols}")

    unified = unified.drop(columns=["_name_norm"], errors="ignore")
    return unified


# ══════════════════════════════════════════════════════════════════════════════
#  Load helpers
# ══════════════════════════════════════════════════════════════════════════════
