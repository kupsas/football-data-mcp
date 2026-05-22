"""Transfermarkt player profiles and related flat tables."""

from __future__ import annotations

import json
import logging
import sys
import time

import pandas as pd

from collect_data.config import SEASONS
from collect_data.helpers import _norm_name
from collect_data.storage import RAW_DIR, repo_root, save_raw

log = logging.getLogger(__name__)


def _parse_tm_value(s: str) -> float | None:
    """Parse '€45.00m' or '€500k' → float in EUR."""
    if not isinstance(s, str):
        return None
    s = s.replace("€", "").replace(",", "").strip()
    try:
        if "m" in s.lower():
            return float(s.lower().replace("m", "").strip()) * 1_000_000
        if "k" in s.lower():
            return float(s.lower().replace("k", "").strip()) * 1_000
        return float(s)
    except (ValueError, AttributeError):
        return None


def _tm_season(season: str, valid_seasons: dict) -> str | None:
    """Map 'YYYY-YYYY' to whatever key Transfermarkt uses (e.g. '24/25')."""
    start, end = season.split("-")
    s2, e2 = start[2:], end[2:]
    for fmt in [f"{s2}/{e2}", f"{start}/{e2}", f"{start}/{end}"]:
        if fmt in valid_seasons:
            return fmt
    for k in valid_seasons:
        if start in k or s2 in k:
            return k
    return None


def collect_transfermarkt(leagues=None, seasons=None, sleep=2.0):
    """Scrape player profiles from Transfermarkt for all supported leagues."""
    sys.path.insert(0, str(repo_root() / "src"))
    from ScraperFC import Transfermarkt
    from ScraperFC.utils import get_module_comps

    all_tm = list(get_module_comps("TRANSFERMARKT").keys())
    leagues = leagues or all_tm
    seasons = seasons or SEASONS
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    tm = Transfermarkt()
    for league in leagues:
        if league not in all_tm:
            log.info(f"  TM: {league!r} not supported, skipping")
            continue

        try:
            valid = tm.get_valid_seasons(league)
        except Exception as e:
            log.warning(f"  TM get_valid_seasons failed for {league}: {e}")
            continue

        for season in seasons:
            tm_year = _tm_season(season, valid)
            if not tm_year:
                log.info(f"  TM: {league} {season} not available (got {list(valid.keys())[:3]})")
                continue

            slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
            flat_path = RAW_DIR / f"transfermarkt__{slug}.parquet"
            if flat_path.exists():
                log.info(f"⏭️  transfermarkt__{slug}.parquet")
                continue

            log.info(f"📡 Transfermarkt: {league} {season} ({tm_year})")
            try:
                player_links = tm.get_player_links(tm_year, league)
            except Exception as e:
                log.error(f"  ❌ get_player_links: {e}")
                continue

            log.info(f"  {len(player_links)} players to scrape")
            flat_rows = []

            for j, link in enumerate(player_links):
                try:
                    pdf = tm.scrape_player(link)
                    if pdf.empty:
                        continue
                    row = pdf.iloc[0]
                    tm_id = str(row.get("ID", ""))

                    flat_rows.append(
                        {
                            "tm_id": tm_id,
                            "tm_name": row.get("Name"),
                            "market_value_str": row.get("Value"),
                            "market_value_eur": _parse_tm_value(str(row.get("Value", ""))),
                            "contract_expiration": row.get("Contract expiration"),
                            "dob": row.get("DOB"),
                            "age": row.get("Age"),
                            "height_m": row.get("Height (m)"),
                            "nationality": row.get("Nationality"),
                            "citizenship": json.dumps(row.get("Citizenship") or []),
                            "tm_position": row.get("Position"),
                            "team": row.get("Team"),
                            "last_club": row.get("Last club"),
                            "joined": row.get("Joined"),
                            "since": row.get("Since"),
                            "league": league,
                            "season": season,
                            "_name_norm": _norm_name(str(row.get("Name", ""))),
                        }
                    )

                    if (j + 1) % 100 == 0:
                        log.info(f"  [{j + 1}/{len(player_links)}] scraped")

                except Exception as e:
                    log.warning(f"  ⚠️  {link}: {e}")

                time.sleep(sleep)

            if flat_rows:
                save_raw(pd.DataFrame(flat_rows), f"transfermarkt__{slug}")
                log.info(f"  ✅ {len(flat_rows)} players")
