"""Capology wage scraping (Selenium via ScraperFC)."""

from __future__ import annotations

import logging
import sys

import pandas as pd

from collect_data.config import SEASONS
from collect_data.storage import RAW_DIR, repo_root, save_raw

log = logging.getLogger(__name__)


def _cap_season(season: str, valid_seasons: list) -> str | None:
    """Map 'YYYY-YYYY' to whatever string Capology uses (e.g. '2024-25')."""
    start, end = season.split("-")
    s2, e2 = start[2:], end[2:]
    for fmt in [
        f"{s2}/{e2}",
        f"{start}/{e2}",
        f"{start}/{end}",
        f"{start}-{e2}",
        f"{start}-{end}",
    ]:
        if fmt in valid_seasons:
            return fmt
    for vs in valid_seasons:
        if start in vs or s2 in vs:
            return vs
    return None


def _flatten_capology(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten Capology multi-index columns to snake_case strings."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    cols = []
    for l0, l1 in df.columns:
        l0c = l0.strip().lower().replace(" ", "_")
        l1c = l1.strip().lower().replace(" ", "_")
        if l1c and l1c != l0c:
            cols.append(f"{l1c}__{l0c}".strip("_"))
        else:
            cols.append(l0c)
    df = df.copy()
    df.columns = cols
    return df


def collect_capology(leagues=None, seasons=None, currency="eur"):
    """Scrape player wages from Capology for all supported leagues."""
    sys.path.insert(0, str(repo_root() / "src"))
    try:
        from ScraperFC import Capology
    except ImportError as e:
        log.error(f"Capology import error (Selenium required): {e}")
        return
    from ScraperFC.utils import get_module_comps

    all_cap = list(get_module_comps("CAPOLOGY").keys())
    leagues = leagues or all_cap
    seasons = seasons or SEASONS
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for league in leagues:
        if league not in all_cap:
            log.info(f"  Capology: {league!r} not supported, skipping")
            continue

        try:
            valid = Capology().get_valid_seasons(league)
        except Exception as e:
            log.warning(f"  Capology get_valid_seasons failed for {league}: {e}")
            continue

        for season in seasons:
            cap_year = _cap_season(season, valid)
            if not cap_year:
                log.info(f"  Capology: {league} {season} not available")
                continue

            slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
            path = RAW_DIR / f"capology__{slug}.parquet"
            if path.exists():
                log.info(f"⏭️  capology__{slug}.parquet")
                continue

            log.info(f"📡 Capology: {league} {season} ({cap_year}) [{currency.upper()}]")
            try:
                df = Capology().scrape_salaries(cap_year, league, currency)
                df = _flatten_capology(df)
                df["league"] = league
                df["season"] = season
                df["currency"] = currency
                save_raw(df, f"capology__{slug}")
                log.info(f"  ✅ {len(df)} players")
            except Exception as e:
                log.error(f"  ❌ Capology {league} {season}: {e}")
