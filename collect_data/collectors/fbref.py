"""FBref player stats collection (ScraperFC + Chrome)."""

from __future__ import annotations

import logging
import random
import sys
import time

import pandas as pd

from collect_data.config import FBREF_COL_RENAME, FBREF_LEAGUES, SEASONS, STAT_CATEGORIES
from collect_data.storage import RAW_DIR, repo_root, save_raw

log = logging.getLogger(__name__)


def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    new_cols, seen = [], {}
    for col in df.columns:
        if isinstance(col, tuple):
            parts = [str(p) for p in col if p and "Unnamed:" not in str(p)]
            name = parts[-1] if parts else str(col[-1])
        else:
            name = str(col)
        clean = FBREF_COL_RENAME.get(name, name)
        if clean in seen:
            if isinstance(col, tuple):
                gp = [str(p) for p in col[:-1] if p and "Unnamed:" not in str(p)]
                sfx = gp[0].lower().replace(" ", "_").replace("+", "_").replace("-", "_") if gp else str(seen[clean])
                clean = f"{clean}_{sfx}"
            seen[clean] = seen.get(clean, 0) + 1
        else:
            seen[clean] = 0
        new_cols.append(clean)
    df = df.copy()
    df.columns = new_cols
    return df


def clean_player_df(df: pd.DataFrame, league: str, season: str, stat_category: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = flatten_columns(df)
    for col in ["player", "Player"]:
        if col in df.columns:
            df = df[df[col].notna() & ~df[col].astype(str).str.match(r"^(Player|Rk)$")]
            break
    skip = {"player", "nation", "pos", "team", "age", "player_id", "team_id", "season", "league", "stat_category"}
    for col in df.columns:
        if col not in skip:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["league"] = league
    df["season"] = season
    df["stat_category"] = stat_category
    return df.reset_index(drop=True)


def collect_fbref(leagues=None, seasons=None, stat_categories=None, wait_time=7):
    """Scrape FBref via ScraperFC (opens real Chrome browser)."""
    sys.path.insert(0, str(repo_root() / "src"))
    from ScraperFC import FBref

    leagues = leagues or FBREF_LEAGUES
    seasons = seasons or SEASONS
    stat_categories = stat_categories or STAT_CATEGORIES

    fbref = FBref(wait_time=wait_time)
    total = len(leagues) * len(seasons) * len(stat_categories)
    done = 0

    for season in seasons:
        for league in leagues:
            try:
                valid = fbref.get_valid_seasons(league)
            except Exception as e:
                log.warning(f"Could not get valid seasons for {league}: {e}")
                continue
            if season not in valid:
                log.warning(f"Season {season!r} not in FBref for {league}")
                continue

            for cat in stat_categories:
                done += 1
                fname = f"{league.replace(' ', '_')}__{season.replace('-', '_')}__{cat.replace(' ', '_')}"
                path = RAW_DIR / f"{fname}.parquet"
                if path.exists():
                    log.info(f"[{done}/{total}] ⏭️  {path.name}")
                    continue

                log.info(f"[{done}/{total}] 📡 FBref: {league} / {season} / {cat}")
                t0 = time.time()
                try:
                    result = fbref.scrape_stats(season, league, cat)
                    player_df = result.get("player", pd.DataFrame())
                    if player_df is not None and not player_df.empty:
                        player_df = clean_player_df(player_df, league, season, cat)
                        save_raw(player_df, fname)
                        log.info(f"  ✅ {len(player_df)} players in {time.time() - t0:.1f}s")
                    else:
                        log.warning("  ⚠️  No player data")
                except Exception as e:
                    log.error(f"  ❌ {e}")

                elapsed = time.time() - t0
                pause = max(0, wait_time - elapsed) + random.uniform(0, 2)
                if pause > 0:
                    time.sleep(pause)
