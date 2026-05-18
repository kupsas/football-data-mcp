"""ClubElo global ratings and fixtures (plain HTTP)."""

from __future__ import annotations

import logging
import re
import sys
from datetime import datetime, timezone
from io import StringIO

import pandas as pd
import requests

from collect_data.storage import RAW_DIR, repo_root, save_raw

log = logging.getLogger(__name__)

CLUBELO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _clubelo_scoreline_probs(row: pd.Series) -> tuple[float, float, float]:
    """Sum R:a-b columns into home win / draw / away win probabilities."""
    hw, dr, aw = 0.0, 0.0, 0.0
    for col, val in row.items():
        if not isinstance(col, str) or not col.startswith("R:"):
            continue
        mid = col[2:]
        if "-" not in mid:
            continue
        try:
            a_str, b_str = mid.split("-", 1)
            ha, aww = int(a_str), int(b_str)
            p = float(val)
        except (ValueError, TypeError):
            continue
        if ha > aww:
            hw += p
        elif ha == aww:
            dr += p
        else:
            aw += p
    return hw, dr, aw


def collect_clubelo(date: str | None = None) -> None:
    """
    Fetch ClubElo global snapshot for one calendar day + upcoming fixtures CSV.
    Uses http://api.clubelo.com (HTTPS often times out from some networks).
    """
    sys.path.insert(0, str(repo_root() / "src"))
    from ScraperFC import ClubElo

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        log.error(f"  ClubElo: invalid date {day!r}, expected YYYY-MM-DD")
        return

    slug_date = day.replace("-", "_")
    global_name = f"clubelo__global__{slug_date}"
    fix_name = f"clubelo__fixtures__{slug_date}"
    global_path = RAW_DIR / f"{global_name}.parquet"
    fix_path = RAW_DIR / f"{fix_name}.parquet"

    ce = ClubElo()

    if not global_path.exists():
        try:
            log.info(f"📡 ClubElo global snapshot: {day}")
            gdf = ce.scrape_date(day)
            gdf = gdf.rename(
                columns={
                    "Rank": "rank",
                    "Club": "club",
                    "Country": "country",
                    "Level": "level",
                    "Elo": "elo",
                    "From": "valid_from",
                    "To": "valid_to",
                }
            )
            gdf["snapshot_date"] = day
            save_raw(gdf, global_name)
        except Exception as e:
            log.error(f"  ❌ ClubElo global {day}: {e}")
    else:
        log.info(f"⏭️  {global_name}.parquet already exists")

    if not fix_path.exists():
        try:
            log.info("📡 ClubElo fixtures (upcoming)")
            url = "http://api.clubelo.com/Fixtures"
            r = requests.get(url, headers=CLUBELO_HEADERS, timeout=30)
            r.raise_for_status()
            fdf = pd.read_csv(StringIO(r.text))
            probs = fdf.apply(_clubelo_scoreline_probs, axis=1, result_type="expand")
            fdf = fdf.rename(
                columns={
                    "Date": "date",
                    "Country": "country",
                    "Home": "home_team",
                    "Away": "away_team",
                }
            )
            fdf["home_win_prob"] = probs[0]
            fdf["draw_prob"] = probs[1]
            fdf["away_win_prob"] = probs[2]
            fdf["snapshot_date"] = day
            keep = [
                c
                for c in fdf.columns
                if c
                in {
                    "date",
                    "country",
                    "home_team",
                    "away_team",
                    "home_win_prob",
                    "draw_prob",
                    "away_win_prob",
                    "snapshot_date",
                }
            ]
            save_raw(fdf[keep], fix_name)
            log.info(f"  ✅ ClubElo fixtures: {len(fdf)} rows")
        except Exception as e:
            log.error(f"  ❌ ClubElo fixtures: {e}")
    else:
        log.info(f"⏭️  {fix_name}.parquet already exists")
