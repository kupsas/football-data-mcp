"""
Coalesce SoFIFA FC25 sources A (aniss7) + C (sametozturkk) into one season frame.

Outer-join on ``(_name_norm, _club_norm)``; stats prefer C then A; ``eafc_id`` from A only.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from collect_data.config import EAFC_OUTPUT_COLUMNS
from collect_data.collectors.eafc import (
    STAGING_DIR,
    _download_kaggle_file,
    _ensure_staging,
    _normalize_eafc_frame,
)
from collect_data.storage import RAW_DIR

log = logging.getLogger(__name__)

# Kaggle staging files for FC25 A+C (no nyagami B).
FC25_SOURCE_A: dict = {
    "owner_slug": "aniss7",
    "dataset_slug": "fifa-player-data-from-sofifa-2025-06-03",
    "file_name": "player-data-full-2025-june.csv",
}
FC25_SOURCE_C: dict = {
    "owner_slug": "sametozturkk",
    "dataset_slug": "ea-sports-fc-25-real-player-data-sofifa-merge",
    "file_name": "new-players-data-full.csv",
}

JOIN_KEY = ["_name_norm", "_club_norm"]

# Columns merged C → A (first non-null). Identity keys handled separately.
_COALESCE_COLS: list[str] = [
    c
    for c in EAFC_OUTPUT_COLUMNS
    if c
    not in (
        "eafc_id",
        "player",
        "_name_norm",
        "_club_norm",
        "season",
    )
]

_PRIOR_SEASON = "2024-2025"
_FC25_SEASON = "2025-2026"
_SCHEMA = "aniss7_2025"


def _load_normalized_csv(csv_path: Path, *, season: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path, low_memory=False)
    return _normalize_eafc_frame(df, season=season, schema=_SCHEMA)


def _series_coalesce(c: pd.Series, a: pd.Series) -> pd.Series:
    """First non-null per row; prefer ``c`` then ``a``."""
    out = c.copy()
    mask = out.isna() | (out.astype(str).str.strip() == "") | (out.astype(str) == "nan")
    out.loc[mask] = a.loc[mask]
    return out


def coalesce_ac_frames(frame_a: pd.DataFrame, frame_c: pd.DataFrame) -> pd.DataFrame:
    """
    Outer join A and C on normalized name + club; one row per player-club key.

    ``eafc_id`` from A; other fields C then A.
    """
    if frame_a.empty and frame_c.empty:
        return pd.DataFrame(columns=EAFC_OUTPUT_COLUMNS)

    for col in JOIN_KEY:
        if col not in frame_a.columns:
            frame_a[col] = ""
        if col not in frame_c.columns:
            frame_c[col] = ""

    merged = frame_a.merge(
        frame_c,
        on=JOIN_KEY,
        how="outer",
        suffixes=("_a", "_c"),
    )

    out: dict[str, pd.Series] = {
        "_name_norm": merged["_name_norm"],
        "_club_norm": merged["_club_norm"],
        "season": pd.Series(_FC25_SEASON, index=merged.index),
    }

    if "eafc_id_a" in merged.columns:
        out["eafc_id"] = pd.to_numeric(merged["eafc_id_a"], errors="coerce")
    else:
        out["eafc_id"] = pd.NA

    if "player_c" in merged.columns and "player_a" in merged.columns:
        out["player"] = _series_coalesce(merged["player_c"], merged["player_a"])
    elif "player_a" in merged.columns:
        out["player"] = merged["player_a"]
    elif "player_c" in merged.columns:
        out["player"] = merged["player_c"]
    else:
        out["player"] = ""

    for col in _COALESCE_COLS:
        ca, cb = f"{col}_c", f"{col}_a"
        if ca in merged.columns and cb in merged.columns:
            out[col] = _series_coalesce(merged[ca], merged[cb])
        elif ca in merged.columns:
            out[col] = merged[ca]
        elif cb in merged.columns:
            out[col] = merged[cb]
        else:
            out[col] = None

    df = pd.DataFrame(out)
    for col in EAFC_OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[EAFC_OUTPUT_COLUMNS]

    sort_col = "overall_rating" if "overall_rating" in df.columns else "eafc_id"
    df = df.sort_values(sort_col, ascending=False, na_position="last")

    eid = pd.to_numeric(df["eafc_id"], errors="coerce")
    has_id = eid.notna() & (eid > 0)
    deduped = pd.concat(
        [
            df.loc[has_id].drop_duplicates(subset=["eafc_id"], keep="first"),
            df.loc[~has_id].drop_duplicates(subset=JOIN_KEY, keep="first"),
        ],
        ignore_index=True,
    )
    return deduped


def _dedupe_eafc_season(df: pd.DataFrame) -> pd.DataFrame:
    """One row per ``eafc_id`` or per name+club when id missing."""
    if df.empty:
        return df
    sort_col = "overall_rating" if "overall_rating" in df.columns else "eafc_id"
    df = df.sort_values(sort_col, ascending=False, na_position="last")
    eid = pd.to_numeric(df["eafc_id"], errors="coerce")
    has_id = eid.notna() & (eid > 0)
    return pd.concat(
        [
            df.loc[has_id].drop_duplicates(subset=["eafc_id"], keep="first"),
            df.loc[~has_id].drop_duplicates(subset=JOIN_KEY, keep="first"),
        ],
        ignore_index=True,
    )


def _is_empty_work_rate(s: pd.Series) -> pd.Series:
    return s.isna() | (s.astype(str).str.strip() == "") | (s.astype(str).str.lower() == "nan")


def fill_work_rates_from_prior_season(
    fc25: pd.DataFrame,
    *,
    prior_parquet: Path | None = None,
    prior_season: str = _PRIOR_SEASON,
) -> pd.DataFrame:
    """
    Copy ``work_rate_attacking`` / ``work_rate_defending`` from prior season where FC25 is empty.

    Join order: ``eafc_id``, then ``(_name_norm, _club_norm)``.
    """
    out = fc25.copy()
    path = prior_parquet or (RAW_DIR / "eafc__2024_2025.parquet")
    if not path.exists():
        log.warning("  Prior season parquet missing (%s); skipping work-rate fill", path.name)
        return out

    prior = _dedupe_eafc_season(pd.read_parquet(path))
    prior = prior[prior["season"] == prior_season] if "season" in prior.columns else prior
    if prior.empty:
        return out

    prior_wr = prior[
        ["eafc_id", "_name_norm", "_club_norm", "work_rate_attacking", "work_rate_defending"]
    ].copy()
    prior_wr["eafc_id"] = pd.to_numeric(prior_wr["eafc_id"], errors="coerce")

    def _both_filled(frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        return int(
            (
                ~_is_empty_work_rate(frame["work_rate_attacking"])
                & ~_is_empty_work_rate(frame["work_rate_defending"])
            ).sum()
        )

    n_before = _both_filled(out)

    # Primary: eafc_id
    out = out.merge(
        prior_wr.add_suffix("_prior"),
        left_on="eafc_id",
        right_on="eafc_id_prior",
        how="left",
    )
    for col in ("work_rate_attacking", "work_rate_defending"):
        empty = _is_empty_work_rate(out[col])
        prior_col = f"{col}_prior"
        if prior_col in out.columns:
            has_prior = ~_is_empty_work_rate(out[prior_col])
            fill_mask = empty & has_prior
            out.loc[fill_mask, col] = out.loc[fill_mask, prior_col]
    out = out.drop(
        columns=[c for c in out.columns if c.endswith("_prior") or c == "eafc_id_prior"],
        errors="ignore",
    )

    # Fallback: name + club for rows still empty
    still_empty = _is_empty_work_rate(out["work_rate_attacking"]) | _is_empty_work_rate(
        out["work_rate_defending"]
    )
    if still_empty.any():
        prior_key = prior_wr.drop_duplicates(subset=JOIN_KEY, keep="first")
        out = out.merge(
            prior_key.add_suffix("_pk"),
            left_on=JOIN_KEY,
            right_on=[f"{k}_pk" for k in JOIN_KEY],
            how="left",
        )
        for col in ("work_rate_attacking", "work_rate_defending"):
            empty = _is_empty_work_rate(out[col])
            prior_col = f"{col}_pk"
            if prior_col in out.columns:
                has_prior = ~_is_empty_work_rate(out[prior_col])
                fill_mask = empty & has_prior
                out.loc[fill_mask, col] = out.loc[fill_mask, prior_col]
        out = out.drop(columns=[c for c in out.columns if c.endswith("_pk")], errors="ignore")

    n_after = _both_filled(out)
    log.info(
        "  Work-rate carry-forward from %s: %s → %s rows with both rates (+%s)",
        prior_season,
        n_before,
        n_after,
        max(0, n_after - n_before),
    )
    return out[EAFC_OUTPUT_COLUMNS]


def download_fc25_staging_csvs() -> tuple[Path, Path]:
    """Ensure A and C CSVs exist under ``_eafc_staging``."""
    staging = _ensure_staging()
    path_a = staging / FC25_SOURCE_A["file_name"]
    path_c = staging / FC25_SOURCE_C["file_name"]
    _download_kaggle_file(
        FC25_SOURCE_A["owner_slug"],
        FC25_SOURCE_A["dataset_slug"],
        FC25_SOURCE_A["file_name"],
        path_a,
    )
    _download_kaggle_file(
        FC25_SOURCE_C["owner_slug"],
        FC25_SOURCE_C["dataset_slug"],
        FC25_SOURCE_C["file_name"],
        path_c,
    )
    return path_a, path_c


def build_fc25_ac_coalesce(*, prior_parquet: Path | None = None) -> pd.DataFrame:
    """Load A+C from staging, coalesce, carry forward work rates from 2024-25."""
    path_a, path_c = download_fc25_staging_csvs()
    log.info("  FC25 coalesce: loading %s and %s", path_a.name, path_c.name)
    frame_a = _load_normalized_csv(path_a, season=_FC25_SEASON)
    frame_c = _load_normalized_csv(path_c, season=_FC25_SEASON)
    log.info("  FC25 coalesce: A=%s rows, C=%s rows", len(frame_a), len(frame_c))
    merged = coalesce_ac_frames(frame_a, frame_c)
    log.info("  FC25 coalesce: merged=%s rows", len(merged))
    return fill_work_rates_from_prior_season(merged, prior_parquet=prior_parquet)
