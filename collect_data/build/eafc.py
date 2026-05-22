"""Merge EA FC attribute parquets into the unified player frame."""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from collect_data.config import EAFC_OUTPUT_COLUMNS, EAFC_UNIFIED_PREFIX, SEASONS
from collect_data.helpers import (
    _club_norm_for_match,
    _norm_name,
    _norm_team,
    _team_clubs_norm_list,
)
from collect_data.storage import DATA_DIR, load_parquets

log = logging.getLogger(__name__)

REEP_PEOPLE_PATH = DATA_DIR / "reference" / "reep_people.csv"

# Debug column: FIFA ``player`` string chosen at merge time (safe to drop later).
EAFC_MATCH_PLAYER_COL = "eafc_match_player"

# Fuzzy pass: club-first, then players only inside matched clubs (same season, top-8 FIFA).
_FUZZY_CLUB_CUTOFF = 78
_FUZZY_PLAYER_IN_CLUB_CUTOFF = 75
_FUZZY_PLAYER_ONLY_CUTOFF = 92  # unified or FIFA row has no club to map

# SoFIFA ``club_league_name`` values for our top 8 domestic leagues (excl. UCL/UEL).
FIFA_TOP8_CLUB_LEAGUES: frozenset[str] = frozenset({
    "Premier League",
    "La Liga",
    "Bundesliga",
    "Serie A",
    "Ligue 1",
    "Eredivisie",
    "Liga Portugal",
    "Championship",
})

# Columns we attach to unified (prefixed); identity keys are not duplicated.
_EAFC_MERGE_COLS = [
    c for c in EAFC_OUTPUT_COLUMNS
    if c not in ("player", "_name_norm", "_club_norm", "season")
]


def load_all_eafc_raw() -> pd.DataFrame:
    return load_parquets("eafc__*.parquet")


def _ensure_eafc_norms(eafc_df: pd.DataFrame) -> pd.DataFrame:
    eafc_df = eafc_df.copy()
    if "_name_norm" not in eafc_df.columns:
        eafc_df["_name_norm"] = eafc_df["player"].apply(_norm_name)
    if "_club_norm" not in eafc_df.columns:
        if "club_name" in eafc_df.columns:
            eafc_df["_club_norm"] = eafc_df["club_name"].apply(_norm_team)
        else:
            eafc_df["_club_norm"] = ""
    return eafc_df


def _load_reep_sofascore_to_sofifa() -> dict[int, int]:
    """
    REEP crosswalk: SofaScore player id -> SoFIFA id (same as our ``eafc_id``).

    Download once to ``data/reference/reep_people.csv`` from the REEP project.
    """
    if not REEP_PEOPLE_PATH.exists():
        log.warning(
            "REEP people.csv missing at %s; EA FC merge falls back to name+season only",
            REEP_PEOPLE_PATH,
        )
        return {}

    reep = pd.read_csv(
        REEP_PEOPLE_PATH,
        usecols=["key_sofascore", "key_sofifa"],
        low_memory=False,
    )
    both = reep.dropna(subset=["key_sofascore", "key_sofifa"]).copy()
    both["key_sofascore"] = pd.to_numeric(both["key_sofascore"], errors="coerce")
    both["key_sofifa"] = pd.to_numeric(both["key_sofifa"], errors="coerce")
    both = both.dropna(subset=["key_sofascore", "key_sofifa"])
    both["key_sofascore"] = both["key_sofascore"].astype(int)
    both["key_sofifa"] = both["key_sofifa"].astype(int)
    both = both.drop_duplicates(subset=["key_sofascore"], keep="first")
    return dict(zip(both["key_sofascore"], both["key_sofifa"]))


def _prefixed(col: str) -> str:
    # ``eafc_id`` is already namespaced; avoid ``eafc_eafc_id``.
    if col in ("eafc_id",) or col.startswith(EAFC_UNIFIED_PREFIX):
        return col
    return f"{EAFC_UNIFIED_PREFIX}{col}"


def _prepare_eafc_merge_table(eafc_df: pd.DataFrame) -> pd.DataFrame:
    """Dedupe EA FC rows and apply unified column prefixes."""
    merge_cols = [
        c for c in _EAFC_MERGE_COLS
        if c in eafc_df.columns and c not in ("eafc_id", "season")
    ]
    rename = {c: _prefixed(c) for c in merge_cols}
    sort_col = "overall_rating" if "overall_rating" in eafc_df.columns else "eafc_id"
    deduped = (
        eafc_df[["eafc_id", "season", "player"] + merge_cols]
        .sort_values(sort_col, ascending=False)
        .drop_duplicates(subset=["eafc_id", "season"], keep="first")
    )
    out = deduped.drop(columns=["player"]).rename(columns=rename)
    out[EAFC_MATCH_PLAYER_COL] = deduped["player"].values
    return out


def _fill_eafc_from_merge(
    unified: pd.DataFrame,
    eafc_table: pd.DataFrame,
    *,
    left_on: list[str],
    right_on: list[str],
) -> pd.DataFrame:
    """Left-merge EA FC attributes and coalesce into ``unified`` (no duplicate columns)."""
    attr_cols = [c for c in eafc_table.columns if c not in right_on]
    out = unified.copy()
    # Drop existing EA FC columns on the left so pandas does not create ``_x`` / ``_y`` suffixes.
    left = out.drop(columns=[c for c in attr_cols if c in out.columns], errors="ignore")
    merged = left.merge(
        eafc_table,
        left_on=left_on,
        right_on=right_on,
        how="left",
    )
    # Column merge resets to RangeIndex; restore left labels so .loc[use]
    # does not write row i of merged onto unified label i (different players).
    if not merged.index.equals(left.index):
        merged.index = left.index
    rating_col = f"{EAFC_UNIFIED_PREFIX}overall_rating"
    for col in attr_cols:
        if col not in merged.columns:
            continue
        new_s = merged[col]
        if col == EAFC_MATCH_PLAYER_COL:
            new_ok = new_s.notna() & (new_s.astype(str).str.strip() != "") & (
                new_s.astype(str) != "nan"
            )
            if rating_col in merged.columns:
                new_rating = pd.to_numeric(merged[rating_col], errors="coerce")
                old_rating = (
                    pd.to_numeric(out[rating_col], errors="coerce")
                    if rating_col in out.columns
                    else pd.Series(0, index=out.index)
                )
                use = new_ok & new_rating.notna() & (new_rating > 0) & (
                    old_rating.fillna(0) <= 0
                )
            else:
                use = new_ok
            if use.any():
                out.loc[use, col] = new_s.loc[use]
            elif col not in out.columns:
                out[col] = new_s
            else:
                out[col] = out[col].fillna(new_s)
            continue
        if col in out.columns:
            # Unified often has 0 placeholders from earlier pipeline steps; fillna alone
            # would skip those. Overwrite when the merge brings a positive numeric value.
            new_num = pd.to_numeric(new_s, errors="coerce")
            old_num = pd.to_numeric(out[col], errors="coerce")
            use = new_num.notna() & (new_num.fillna(0) > 0) & (old_num.fillna(0) <= 0)
            if use.any():
                out.loc[use, col] = new_s.loc[use]
            else:
                out[col] = out[col].fillna(new_s)
        else:
            out[col] = new_s
    return out


def _filter_eafc_top8_leagues(eafc_df: pd.DataFrame) -> pd.DataFrame:
    """Keep only FIFA rows tagged with one of our top 8 domestic ``club_league_name`` values."""
    if "club_league_name" not in eafc_df.columns:
        return eafc_df.iloc[0:0]
    return eafc_df[eafc_df["club_league_name"].isin(FIFA_TOP8_CLUB_LEAGUES)].copy()


def _top8_club_norms_by_season(eafc_df: pd.DataFrame) -> dict[str, set[str]]:
    """``_club_norm`` values that appear in a top-8 FIFA league for each ``season``."""
    strict = _filter_eafc_top8_leagues(eafc_df)
    if strict.empty or "season" not in strict.columns:
        return {}
    out: dict[str, set[str]] = {}
    for season, grp in strict.groupby("season", sort=False):
        out[str(season)] = _top8_club_norms(grp)
    return out


def _filter_eafc_top8_with_border(
    eafc_df: pd.DataFrame,
    seasons: list[str] | None = None,
    *,
    log_border: bool = True,
) -> pd.DataFrame:
    """
    Top-8 FIFA rows plus promotion/relegation border clubs for each season.

    SoFIFA sometimes tags a club in a second tier in season *X* while the adjacent
    season *X±1* lists the same club in a top-8 league (e.g. Darmstadt 98 in
    ``2. Bundesliga`` for 2023-24 but ``Bundesliga`` for 2024-25). Those ~3
    promoted/relegated sides per season per league are included in the fuzzy pool
    for *X* when their ``_club_norm`` matches a top-8 club norm from *X-1* or *X+1*.
    """
    pool = seasons or SEASONS
    strict = _filter_eafc_top8_leagues(eafc_df)
    if strict.empty or "season" not in eafc_df.columns or "_club_norm" not in eafc_df.columns:
        return strict

    top8_by_season = _top8_club_norms_by_season(eafc_df)
    extra_parts: list[pd.DataFrame] = []

    for season in pool:
        border: set[str] = set()
        for adj in _adjacent_seasons(season, pool):
            border |= top8_by_season.get(adj, set())
        if not border:
            continue

        bucket = eafc_df[eafc_df["season"] == season]
        if bucket.empty or "club_league_name" not in bucket.columns:
            continue
        non_top8 = bucket[~bucket["club_league_name"].isin(FIFA_TOP8_CLUB_LEAGUES)]
        if non_top8.empty:
            continue

        border_list = sorted(border)
        non_top8_clubs = sorted(
            {str(c) for c in non_top8["_club_norm"].unique() if str(c)}
        )
        to_border = _map_clubs_unified_to_fifa(non_top8_clubs, border_list)
        hit = non_top8["_club_norm"].astype(str).apply(
            lambda cn: bool(cn)
            and (cn in border or to_border.get(cn, "") in border)
        )
        picked = non_top8.loc[hit]
        if not picked.empty:
            extra_parts.append(picked)

    if not extra_parts:
        return strict

    expanded = pd.concat([strict, *extra_parts], ignore_index=False)
    expanded = expanded[~expanded.index.duplicated(keep="first")]
    n_extra = len(expanded) - len(strict)
    if n_extra and log_border:
        log.info(
            "    EA FC top-8 pool +%s rows for promotion/relegation border clubs",
            n_extra,
        )
    return expanded


def _top8_club_norms(bucket_top8: pd.DataFrame) -> set[str]:
    if bucket_top8.empty or "_club_norm" not in bucket_top8.columns:
        return set()
    return {str(c) for c in bucket_top8["_club_norm"].unique() if str(c)}


def _unified_club_top8_qualified(
    u_club: str,
    club_map: dict[str, str],
    top8_norms: set[str],
) -> bool:
    """True when this unified club is (or maps to) a FIFA club in the top-8 league pool."""
    if u_club in top8_norms:
        return True
    f_club = club_map.get(u_club)
    return bool(f_club and f_club in top8_norms)


def _loan_expand_multi_club(
    club_norms: list[str],
    club_map: dict[str, str],
    top8_norms: set[str],
) -> bool:
    """
    Widen FIFA search to non-top-8 clubs only for comma-separated team cells where
    at least one listed club is in (or maps to) a top-8 league club.
    """
    if len(club_norms) <= 1:
        return False
    return any(
        _unified_club_top8_qualified(c, club_map, top8_norms) for c in club_norms
    )


def _dedupe_eafc_season_bucket(bucket: pd.DataFrame) -> pd.DataFrame:
    if bucket.empty:
        return bucket
    sort_col = (
        "overall_rating"
        if "overall_rating" in bucket.columns
        else "eafc_id"
    )
    return bucket.sort_values(sort_col, ascending=False).drop_duplicates(
        subset=["eafc_id"],
        keep="first",
    )


def _club_tokens_compatible(u_club: str, f_club: str) -> bool:
    """Reject fuzzy club pairs that only share a generic token (e.g. real valladolid → real betis)."""
    tokens = [t for t in u_club.split() if len(t) > 3]
    if not tokens:
        return True
    return all(t in f_club for t in tokens)


def _map_clubs_unified_to_fifa(
    unified_clubs: list[str],
    fifa_clubs: list[str],
    *,
    cutoff: float = _FUZZY_CLUB_CUTOFF,
) -> dict[str, str]:
    """Map each unified ``_club_norm`` to the best FIFA ``_club_norm`` at or above ``cutoff``."""
    from rapidfuzz import process

    if not unified_clubs or not fifa_clubs:
        return {}

    fifa_set = set(fifa_clubs)
    mapping: dict[str, str] = {}
    for u_club in unified_clubs:
        if u_club in fifa_set:
            mapping[u_club] = u_club
            continue
        hit = process.extractOne(
            u_club,
            fifa_clubs,
            score_cutoff=cutoff,
        )
        if hit is None:
            continue
        f_club = hit[0]
        if _club_tokens_compatible(u_club, f_club):
            mapping[u_club] = f_club
    return mapping


def _fifa_players_by_club(bucket: pd.DataFrame) -> dict[str, list[Any]]:
    """Index FIFA row indices by ``_club_norm`` for fast per-club player search."""
    by_club: dict[str, list[Any]] = {}
    for idx, row in bucket.iterrows():
        club = str(row.get("_club_norm", "") or "")
        if not club:
            continue
        by_club.setdefault(club, []).append(idx)
    return by_club


def _fuzzy_eafc_fill(
    unmatched: pd.DataFrame,
    eafc_df: pd.DataFrame,
    prefixed_cols: list[str],
    *,
    has_club_data: bool,
) -> pd.DataFrame:
    """
    Club-first fuzzy merge (same season).

    Default FIFA pool: top-8 leagues plus promotion/relegation border clubs (see
    ``_filter_eafc_top8_with_border``). For comma-separated ``team`` cells where
    at least one club is in that pool, also search other clubs in the same cell
    (e.g. loan at Real Valladolid in La Liga 2 when Osasuna is listed).

    1. Map unified clubs → FIFA club (≥78%) using the full-season club list.
    2. Per row: try player match at 1st club, then 2nd, etc. (≥75%).
    3. If no club path succeeds, strict player-only search in the top-8 pool (92%).
    """
    from rapidfuzz import process

    if unmatched.empty:
        return unmatched

    eafc_top8 = _filter_eafc_top8_with_border(eafc_df)
    if eafc_top8.empty:
        log.warning("  Fuzzy EA FC: no top-8 league rows in FIFA data; skipping fuzzy pass")
        return unmatched

    merge_cols = [c for c in _EAFC_MERGE_COLS if c in eafc_df.columns]
    rename = {c: _prefixed(c) for c in merge_cols}
    attr_cols = [rename[c] for c in merge_cols if c in rename]

    filled = unmatched.copy()
    for col in attr_cols:
        if col not in filled.columns:
            filled[col] = pd.NA
    if EAFC_MATCH_PLAYER_COL not in filled.columns:
        filled[EAFC_MATCH_PLAYER_COL] = pd.NA

    via_club = 0
    via_club_second_plus = 0
    via_club_loan_league = 0
    player_only = 0
    club_pairs = 0

    for season, u_group in unmatched.groupby("season", sort=False):
        bucket_full = _dedupe_eafc_season_bucket(eafc_df[eafc_df["season"] == season])
        bucket_top8 = _dedupe_eafc_season_bucket(
            eafc_top8[eafc_top8["season"] == season]
        )
        if bucket_top8.empty:
            continue

        fifa_clubs = sorted(
            {str(c) for c in bucket_full["_club_norm"].unique() if str(c)}
        )
        top8_norms = _top8_club_norms(bucket_top8)
        players_top8 = _fifa_players_by_club(bucket_top8)
        players_full = _fifa_players_by_club(bucket_full)

        unified_club_set: set[str] = set()
        if "team" in u_group.columns:
            for team_val in u_group["team"]:
                unified_club_set.update(_team_clubs_norm_list(str(team_val or "")))
        else:
            unified_club_set.update(
                str(c) for c in u_group["_club_norm"].unique() if str(c)
            )
        unified_clubs = sorted(unified_club_set)
        club_map = (
            _map_clubs_unified_to_fifa(unified_clubs, fifa_clubs)
            if has_club_data and unified_clubs and fifa_clubs
            else {}
        )
        club_pairs += len(club_map)

        # Player-only pool stays top-8 only (no widening for loans).
        all_names = bucket_top8["_name_norm"].astype(str).tolist()
        all_idxs = bucket_top8.index.tolist()

        for u_idx, u_row in u_group.iterrows():
            u_pn = str(u_row.get("_name_norm", "") or "")
            if not u_pn:
                continue

            team_val = str(u_row.get("team", "") or "") if "team" in u_row.index else ""
            club_norms = _team_clubs_norm_list(team_val)
            if not club_norms:
                u_tn = str(u_row.get("_club_norm", "") or "")
                if u_tn:
                    club_norms = [u_tn]

            loan_expand = _loan_expand_multi_club(club_norms, club_map, top8_norms)
            players_by_club = players_full if loan_expand else players_top8
            bucket = bucket_full if loan_expand else bucket_top8

            best_eafc_idx: Any = None
            src_bucket = bucket_top8
            used_club_path = False
            matched_club_pass = -1
            matched_f_club = ""

            name_cutoff = 70 if len(u_pn) <= 5 else _FUZZY_PLAYER_IN_CLUB_CUTOFF
            for pass_i, u_club in enumerate(club_norms):
                f_club = club_map.get(u_club)
                if not f_club or f_club not in players_by_club:
                    continue
                club_idxs = players_by_club[f_club]
                club_names = [str(bucket.at[i, "_name_norm"]) for i in club_idxs]
                hit = process.extractOne(
                    u_pn,
                    club_names,
                    score_cutoff=name_cutoff,
                )
                if hit is not None:
                    _name, _score, pos = hit
                    best_eafc_idx = club_idxs[pos]
                    src_bucket = bucket
                    used_club_path = True
                    matched_club_pass = pass_i
                    matched_f_club = f_club
                    break

            if best_eafc_idx is None and has_club_data:
                hit = process.extractOne(
                    u_pn,
                    all_names,
                    score_cutoff=_FUZZY_PLAYER_ONLY_CUTOFF,
                )
                if hit is not None:
                    _name, _score, pos = hit
                    best_eafc_idx = all_idxs[pos]
                    src_bucket = bucket_top8

            if best_eafc_idx is None:
                continue

            if used_club_path:
                via_club += 1
                if matched_club_pass > 0:
                    via_club_second_plus += 1
                if loan_expand and matched_f_club not in top8_norms:
                    via_club_loan_league += 1
            else:
                player_only += 1

            src = src_bucket.loc[best_eafc_idx]
            if "player" in src.index:
                filled.at[u_idx, EAFC_MATCH_PLAYER_COL] = src["player"]
            for col in merge_cols:
                dst = rename[col]
                if col in src.index:
                    filled.at[u_idx, dst] = src[col]

    log.info(
        "    Fuzzy pool: %s FIFA rows (top 8 + border); %s unified→FIFA club pairs (≥%.0f%%)",
        len(eafc_top8),
        club_pairs,
        _FUZZY_CLUB_CUTOFF,
    )
    if via_club or player_only:
        log.info(
            "    Fuzzy detail: %s via club→player (%s on 2nd+ club, %s loan/non-top-8 club), %s player-only",
            via_club,
            via_club_second_plus,
            via_club_loan_league,
            player_only,
        )
    return filled


def _propagate_eafc_by_sofascore_season(
    unified: pd.DataFrame,
    key_col: str,
) -> tuple[pd.DataFrame, int]:
    """
    Copy EA FC columns onto every unified row for the same ``sofascore_id`` + ``season``.

    Unified has one row per (player, league, season); FIFA attributes are per player-season.
    When any league row matched, sibling rows (UCL, cup, old club league) get the same values.
    """
    if "sofascore_id" not in unified.columns or "season" not in unified.columns:
        return unified, 0

    eafc_cols = [
        c for c in unified.columns
        if c.startswith(EAFC_UNIFIED_PREFIX) or c == EAFC_MATCH_PLAYER_COL
    ]
    if not eafc_cols or key_col not in eafc_cols:
        return unified, 0

    ss_id = pd.to_numeric(unified["sofascore_id"], errors="coerce")
    eligible = ss_id.notna() & (ss_id > 0)
    if not eligible.any():
        return unified, 0

    rating = pd.to_numeric(unified[key_col], errors="coerce").fillna(0)
    n_propagated = 0

    subset = unified.loc[eligible]
    for (_, _), grp in subset.groupby(
        [ss_id.loc[eligible], subset["season"]],
        sort=False,
    ):
        hit_mask = rating.loc[grp.index] > 0
        if not hit_mask.any():
            continue
        src_idx = rating.loc[grp.index][hit_mask].idxmax()
        miss_idx = grp.index[~hit_mask]
        if len(miss_idx) == 0:
            continue
        for col in eafc_cols:
            unified.loc[miss_idx, col] = unified.loc[src_idx, col]
        n_propagated += len(miss_idx)

    return unified, n_propagated


def _season_start_year(season: str) -> int:
    return int(str(season).split("-")[0])


def _adjacent_seasons(season: str, seasons: list[str] | None = None) -> list[str]:
    """Return seasons in ``seasons`` whose start year is ±1 from ``season``."""
    pool = seasons or SEASONS
    y = _season_start_year(season)
    return [s for s in pool if abs(_season_start_year(s) - y) == 1]


def _seasons_chronological(seasons: list[str] | None = None) -> list[str]:
    """Configured seasons sorted oldest → newest."""
    pool = seasons or list(SEASONS)
    return sorted(pool, key=_season_start_year)


def _future_seasons(season: str, seasons: list[str] | None = None) -> list[str]:
    """Seasons strictly after ``season`` (nearest future year first)."""
    chrono = _seasons_chronological(seasons)
    y = _season_start_year(season)
    return [s for s in chrono if _season_start_year(s) > y]


def _apply_eafc_source_row(
    unified: pd.DataFrame,
    row_idxs: pd.Index,
    fifa_row: pd.Series,
    merge_cols: list[str],
    rename: dict[str, str],
    *,
    fifa_player_name: str | None = None,
) -> None:
    """Write one deduped FIFA row onto unified indices."""
    display_name = fifa_player_name
    if not display_name:
        if "player" in fifa_row.index:
            display_name = str(fifa_row["player"])
        elif fifa_row.name is not None:
            # ``set_index("player")`` lookup — name lives on the index, not columns.
            display_name = str(fifa_row.name)
    if display_name:
        unified.loc[row_idxs, EAFC_MATCH_PLAYER_COL] = display_name
    for col in merge_cols:
        dst = rename[col]
        if col in fifa_row.index:
            unified.loc[row_idxs, dst] = fifa_row[col]


def _fifa_row_by_player_name(
    eafc_df: pd.DataFrame,
    season: str,
    fifa_name: str,
) -> pd.Series | None:
    """Best FIFA row for ``player`` in ``season`` (highest ``overall_rating``)."""
    if not fifa_name or not str(fifa_name).strip():
        return None
    bucket = _dedupe_eafc_season_bucket(eafc_df[eafc_df["season"] == season])
    if bucket.empty or "player" not in bucket.columns:
        return None
    hits = bucket[bucket["player"] == fifa_name]
    if hits.empty:
        return None
    return hits.iloc[0]


def _fifa_row_by_eafc_id(
    eafc_df: pd.DataFrame,
    season: str,
    eafc_id: Any,
) -> pd.Series | None:
    """Best FIFA row for ``eafc_id`` in ``season``."""
    eid = pd.to_numeric(pd.Series([eafc_id]), errors="coerce").iloc[0]
    if pd.isna(eid) or eid <= 0:
        return None
    bucket = _dedupe_eafc_season_bucket(eafc_df[eafc_df["season"] == season])
    if bucket.empty:
        return None
    hits = bucket[pd.to_numeric(bucket["eafc_id"], errors="coerce") == eid]
    if hits.empty:
        return None
    return hits.iloc[0]


def _resolve_gap_fifa_row(
    eafc_df: pd.DataFrame,
    gap_season: str,
    fifa_name: str,
    anchor_fifa_row: pd.Series,
) -> tuple[pd.Series | None, str, str]:
    """
    Pick FIFA stats for a gap season.

    Returns ``(row, display_name, method)`` where ``method`` is
    ``exact``, ``eafc_id``, or ``anchor``.

    1. Same ``player`` name in ``gap_season`` (ideal — correct-year card).
    2. Same ``eafc_id`` in ``gap_season`` (name changed, id stable).
    3. ``anchor_fifa_row`` from the future season (no gap-year row; stale attributes).
    """
    row = _fifa_row_by_player_name(eafc_df, gap_season, fifa_name)
    if row is not None:
        return row, str(row.get("player", fifa_name)), "exact"

    anchor_id = anchor_fifa_row.get("eafc_id") if anchor_fifa_row is not None else None
    row = _fifa_row_by_eafc_id(eafc_df, gap_season, anchor_id)
    if row is not None:
        return row, str(row.get("player", fifa_name)), "eafc_id"

    if anchor_fifa_row is not None:
        return anchor_fifa_row, fifa_name, "anchor"
    return None, fifa_name, ""


def _eafc_forward_anchor_backfill(
    unified: pd.DataFrame,
    eafc_df: pd.DataFrame,
    prefixed_cols: list[str],
    key_col: str,
) -> tuple[pd.DataFrame, int]:
    """
    Backfill missing seasons using the nearest future season that already matched.

    Process newer gap seasons first so an earlier year can anchor off a row filled
    in the same pass (e.g. 2024-25 from 2025-26, then 2023-24 from 2024-25).
    """
    if "sofascore_id" not in unified.columns or "season" not in unified.columns:
        return unified, 0

    ss_id = pd.to_numeric(unified["sofascore_id"], errors="coerce")
    eligible = ss_id.notna() & (ss_id > 0)
    if not eligible.any():
        return unified, 0

    rating = pd.to_numeric(unified[key_col], errors="coerce").fillna(0)
    match_name = unified[EAFC_MATCH_PLAYER_COL]
    name_ok = match_name.notna() & (match_name.astype(str).str.strip() != "") & (
        match_name.astype(str) != "nan"
    )

    merge_cols = [c for c in _EAFC_MERGE_COLS if c in eafc_df.columns]
    rename = {c: _prefixed(c) for c in merge_cols}

    n_filled = 0
    n_players = 0
    via_exact = 0
    via_eafc_id = 0
    via_anchor_row = 0

    # Newest → oldest so 2024-25 is filled before 2023-24 looks for anchors.
    for gap_season in reversed(_seasons_chronological()):
        future = _future_seasons(gap_season)
        if not future:
            continue

        gap_missing = eligible & (unified["season"] == gap_season) & (rating <= 0)
        if not gap_missing.any():
            continue

        for sid, grp in unified.loc[gap_missing].groupby(ss_id.loc[gap_missing]):
            sid = float(sid)
            anchor_fifa_name: str | None = None
            anchor_fifa_row: pd.Series | None = None

            for anchor_season in future:
                anchor_mask = (
                    eligible
                    & (ss_id == sid)
                    & (unified["season"] == anchor_season)
                    & (rating > 0)
                    & name_ok
                )
                if not anchor_mask.any():
                    continue
                src_idx = rating.loc[unified.index[anchor_mask]].idxmax()
                candidate_name = str(
                    unified.at[src_idx, EAFC_MATCH_PLAYER_COL] or ""
                ).strip()
                if not candidate_name or candidate_name == "nan":
                    continue
                candidate_row = _fifa_row_by_player_name(
                    eafc_df, anchor_season, candidate_name
                )
                if candidate_row is None:
                    continue
                anchor_fifa_name = candidate_name
                anchor_fifa_row = candidate_row
                break

            if not anchor_fifa_name or anchor_fifa_row is None:
                continue

            gap_row, display_name, method = _resolve_gap_fifa_row(
                eafc_df,
                gap_season,
                anchor_fifa_name,
                anchor_fifa_row,
            )
            if gap_row is None or not method:
                continue

            if method == "exact":
                via_exact += 1
            elif method == "eafc_id":
                via_eafc_id += 1
            else:
                via_anchor_row += 1

            _apply_eafc_source_row(
                unified,
                grp.index,
                gap_row,
                merge_cols,
                rename,
                fifa_player_name=display_name,
            )
            # Refresh rating so later gap seasons in this pass can anchor off this fill.
            rating.loc[grp.index] = pd.to_numeric(
                unified.loc[grp.index, key_col], errors="coerce"
            ).fillna(0)
            n_filled += len(grp)
            n_players += 1

    if n_filled:
        log.info(
            "    Forward anchor backfill: %s rows, %s players"
            " (%s exact name, %s eafc_id, %s anchor-season row)",
            n_filled,
            n_players,
            via_exact,
            via_eafc_id,
            via_anchor_row,
        )
    return unified, n_filled


def merge_eafc_data(unified: pd.DataFrame) -> pd.DataFrame:
    """
    Left-merge EA FC attributes onto unified players.

    1. REEP ``sofascore_id`` -> ``eafc_id`` + season
    2. Exact ``_name_norm`` + season
    3. Fuzzy player + club (top-8 + promotion/relegation border clubs) + season
    4. Propagate to all rows sharing ``sofascore_id`` + ``season``
    5. Forward anchor backfill: missing season ← nearest future match (name / id / row)
    6. Propagate again (same function as step 4)
    """
    eafc_df = load_all_eafc_raw()
    if eafc_df.empty:
        log.info("  No EA FC parquet files; skipping attribute merge")
        return unified

    unified = unified.copy()
    if "_name_norm" not in unified.columns:
        unified["_name_norm"] = unified["player"].apply(_norm_name)
    # Primary club for display; fuzzy tries every club in comma-separated ``team`` cells.
    if "team" in unified.columns:
        unified["_club_norm"] = unified["team"].apply(_club_norm_for_match)
    elif "_club_norm" not in unified.columns:
        unified["_club_norm"] = ""

    eafc_df = _ensure_eafc_norms(eafc_df)
    has_club_data = bool(
        eafc_df["_club_norm"].astype(str).str.len().gt(0).any()
        if "_club_norm" in eafc_df.columns
        else False
    )
    if not has_club_data:
        log.warning(
            "  EA FC parquets lack club_name — re-run: python -m collect_data --eafc-only --force-eafc"
        )

    eafc_table = _prepare_eafc_merge_table(eafc_df)
    prefixed_cols = [
        c for c in eafc_table.columns if c not in ("eafc_id", "season")
    ]
    key_col = f"{EAFC_UNIFIED_PREFIX}overall_rating"
    if key_col not in prefixed_cols and prefixed_cols:
        key_col = prefixed_cols[0]

    if EAFC_MATCH_PLAYER_COL not in unified.columns:
        unified[EAFC_MATCH_PLAYER_COL] = pd.NA

    for col in prefixed_cols:
        if col not in unified.columns:
            unified[col] = pd.NA

    def _has_eafc(row_slice: pd.DataFrame) -> int:
        if key_col not in row_slice.columns:
            return 0
        col = pd.to_numeric(row_slice[key_col], errors="coerce")
        return int((col.fillna(0) > 0).sum())

    def _missing_mask(frame: pd.DataFrame) -> pd.Series:
        if key_col not in frame.columns:
            return pd.Series(True, index=frame.index)
        return pd.to_numeric(frame[key_col], errors="coerce").fillna(0) <= 0

    before = _has_eafc(unified)

    bridge = _load_reep_sofascore_to_sofifa()
    after_id = before
    if bridge and "sofascore_id" in unified.columns:
        ss = pd.to_numeric(unified["sofascore_id"], errors="coerce")
        unified["_eafc_join_id"] = ss.map(bridge)
        has_bridge = unified["_eafc_join_id"].notna()
        if has_bridge.any():
            subset = unified.loc[has_bridge].copy()
            filled = _fill_eafc_from_merge(
                subset,
                eafc_table,
                left_on=["_eafc_join_id", "season"],
                right_on=["eafc_id", "season"],
            )
            for col in prefixed_cols:
                unified.loc[subset.index, col] = filled[col]
        unified.drop(columns=["_eafc_join_id"], errors="ignore", inplace=True)
        after_id = _has_eafc(unified)

    still_missing = _missing_mask(unified)
    after_name = after_id
    if still_missing.any():
        merge_cols = [
            c for c in _EAFC_MERGE_COLS
            if c in eafc_df.columns and c not in ("_name_norm", "season")
        ]
        rename = {c: _prefixed(c) for c in merge_cols}
        sort_col = (
            "overall_rating" if "overall_rating" in eafc_df.columns else "eafc_id"
        )
        name_deduped = (
            eafc_df[["_name_norm", "season", "player"] + merge_cols]
            .sort_values(sort_col, ascending=False)
            .drop_duplicates(subset=["_name_norm", "season"], keep="first")
        )
        eafc_by_name = name_deduped.drop(columns=["player"]).rename(columns=rename)
        eafc_by_name[EAFC_MATCH_PLAYER_COL] = name_deduped["player"].values
        subset = unified.loc[still_missing].copy()
        filled = _fill_eafc_from_merge(
            subset,
            eafc_by_name,
            left_on=["_name_norm", "season"],
            right_on=["_name_norm", "season"],
        )
        for col in prefixed_cols:
            unified.loc[subset.index, col] = filled[col]
        after_name = _has_eafc(unified)

    still_missing = _missing_mask(unified)
    after_fuzzy = after_name
    if still_missing.any():
        subset = unified.loc[still_missing].copy()
        fuzzy_filled = _fuzzy_eafc_fill(
            subset,
            eafc_df,
            prefixed_cols,
            has_club_data=has_club_data,
        )
        new_rating = pd.to_numeric(fuzzy_filled.get(key_col), errors="coerce")
        fuzzy_hits = new_rating.notna() & (new_rating > 0)
        if fuzzy_hits.any():
            hit_idx = fuzzy_hits.index[fuzzy_hits]
            old_rating = pd.to_numeric(
                unified.loc[hit_idx, key_col], errors="coerce"
            ).fillna(0)
            write_idx = hit_idx[old_rating <= 0]
            if len(write_idx):
                for col in prefixed_cols:
                    if col in fuzzy_filled.columns:
                        unified.loc[write_idx, col] = fuzzy_filled.loc[write_idx, col]
                if EAFC_MATCH_PLAYER_COL in fuzzy_filled.columns:
                    unified.loc[write_idx, EAFC_MATCH_PLAYER_COL] = (
                        fuzzy_filled.loc[write_idx, EAFC_MATCH_PLAYER_COL]
                    )
        after_fuzzy = _has_eafc(unified)

    before_prop = _has_eafc(unified)
    unified, n_propagated = _propagate_eafc_by_sofascore_season(unified, key_col)
    after_prop = _has_eafc(unified)

    before_edge = after_prop
    unified, n_edge = _eafc_forward_anchor_backfill(
        unified,
        eafc_df,
        prefixed_cols,
        key_col,
    )
    after_edge = _has_eafc(unified)

    before_prop2 = after_edge
    unified, n_propagated2 = _propagate_eafc_by_sofascore_season(unified, key_col)
    after_prop2 = _has_eafc(unified)

    total = after_prop2
    log.info(
        "  EA FC merge: %s/%s rows with attributes (%s REEP id, %s exact name, %s fuzzy, %s propagated, %s forward anchor)",
        total,
        len(unified),
        after_id - before,
        after_name - after_id,
        after_fuzzy - after_name,
        (after_prop - before_prop) + (after_prop2 - before_prop2),
        after_edge - before_edge,
    )
    if n_propagated or n_propagated2:
        log.info(
            "    Propagated EA FC to %s + %s league/competition rows (same sofascore_id + season)",
            n_propagated,
            n_propagated2,
        )
    return unified
