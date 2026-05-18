"""Canary: Understat ``getLeagueData`` JSON still returns the buckets we parse."""

from __future__ import annotations

import pytest
import requests

# Historical EPL season — first half of ``2023/2024`` maps to calendar year ``2023``
# in ``Understat.scrape_season_data`` (``year.split('/')[0]``).
LEAGUE_CODE = "EPL"
SEASON_YEAR = "2023"
SEASON_REFERER = f"https://understat.com/league/{LEAGUE_CODE}/{SEASON_YEAR}"
UNDERSTAT_LEAGUE_URL = f"https://understat.com/getLeagueData/{LEAGUE_CODE}/{SEASON_YEAR}"

# Understat rejects bare scrapers; match ``_ajax_get`` in ``understat.py`` so we hit the same path.
_UNDERSTAT_AJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": SEASON_REFERER,
}


@pytest.mark.contract
def test_understat_get_league_data_shape() -> None:
    """Assert the JSON keys ``scrape_season_data`` maps into matches/teams/players data."""
    response = requests.get(
        UNDERSTAT_LEAGUE_URL,
        headers=_UNDERSTAT_AJAX_HEADERS,
        timeout=45,
    )
    response.raise_for_status()
    data = response.json()

    # Upstream renamed legacy ``datesData`` / ``teamsData`` / ``playersData``; the scraper
    # expects the modern short keys (see ``ScraperFC.understat.scrape_season_data``).
    assert isinstance(data.get("dates"), list), "`dates` must remain a list (match rounds)."
    assert isinstance(data.get("teams"), dict), "`teams` must remain a dict."
    assert isinstance(data.get("players"), list), "`players` must remain a list of player stat dicts."
