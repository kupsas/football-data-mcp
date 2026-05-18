"""SofaScore season-level player stats (ScraperFC + Chrome)."""

from __future__ import annotations

import logging
import sys
import time

import pandas as pd

from collect_data.config import SEASONS, SOFASCORE_SEASONS, SOFASCORE_TARGET_LEAGUES, SS_COL_RENAME
from collect_data.helpers import _norm_name, _silence_bota_noise, _ss_retry
from collect_data.storage import RAW_DIR, raw_freshness_age_hours, repo_root, save_raw

log = logging.getLogger(__name__)

# Re-scrape SofaScore *season* aggregates for the current season if older than this (hours).
SOFASCORE_SEASON_REFRESH_HOURS = 168.0  # 7 days — suitable for weekly cron


def collect_sofascore(leagues=None, seasons=None):
    """
    Collect all player stats from SofaScore via ScraperFC.
    Runs headless Chrome (botasaurus) — no visible browser window.
    Covers 37 leagues/competitions including UCL, Europa League, Eredivisie, etc.
    """
    sys.path.insert(0, str(repo_root() / "src"))
    from ScraperFC import Sofascore
    from ScraperFC.utils import get_module_comps

    all_ss_leagues = list(get_module_comps("SOFASCORE").keys())
    leagues = leagues or SOFASCORE_TARGET_LEAGUES
    seasons = seasons or SEASONS

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ss = Sofascore()

    total = len(leagues) * len(seasons)
    done = 0

    for league in leagues:
        if league not in all_ss_leagues:
            log.info(f"  SofaScore: {league!r} not supported, skipping")
            continue

        try:
            with _silence_bota_noise():
                valid = _ss_retry(
                    ss.get_valid_seasons,
                    league,
                    label=f"get_valid_seasons({league})",
                )
        except Exception as e:
            log.warning(f"  SofaScore get_valid_seasons failed for {league}: {e}")
            continue

        for season in seasons:
            done += 1
            ss_year = SOFASCORE_SEASONS.get(season)
            if not ss_year or ss_year not in valid:
                log.info(f"[{done}/{total}] ⏭️  {league} {season} — not in SofaScore ({ss_year!r})")
                continue

            fname = f"sofascore__{league.replace(' ', '_')}__{season.replace('-', '_')}"
            raw_path = RAW_DIR / f"{fname}.parquet"
            if raw_path.exists():
                if season != SEASONS[0]:
                    log.info(f"[{done}/{total}] ⏭️  {raw_path.name}")
                    continue
                age_h = raw_freshness_age_hours(fname)
                if age_h is not None and age_h < SOFASCORE_SEASON_REFRESH_HOURS:
                    log.info(
                        f"[{done}/{total}] ⏭️  {raw_path.name} "
                        f"(current season, refreshed {age_h:.1f}h ago)"
                    )
                    continue

            log.info(f"[{done}/{total}] 📡 SofaScore: {league} {season} ({ss_year})")
            t0 = time.time()
            try:
                with _silence_bota_noise():
                    df = ss.scrape_player_league_stats(ss_year, league, accumulation="total")

                if df is None or df.empty:
                    log.warning(f"  ⚠️  Empty result for {league} {season}")
                    continue

                df = df.rename(columns=SS_COL_RENAME)
                df = df.loc[:, ~df.columns.duplicated(keep="first")]

                skip = {
                    "player",
                    "team",
                    "league",
                    "season",
                    "_name_norm",
                    "sofascore_id",
                    "sofascore_team_id",
                }
                for col in df.columns:
                    if col not in skip:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                df["league"] = league
                df["season"] = season
                df["_name_norm"] = df["player"].apply(_norm_name)

                save_raw(df, fname)
                log.info(f"  ✅ {len(df)} players in {time.time() - t0:.1f}s")

            except Exception as e:
                log.error(f"  ❌ SofaScore {league} {season}: {e}")

            time.sleep(1)
