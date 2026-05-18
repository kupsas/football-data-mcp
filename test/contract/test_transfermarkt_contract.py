"""Canary: Transfermarkt league HTML still exposes the season ``<select>`` we scrape."""

from __future__ import annotations

from bs4 import BeautifulSoup
import cloudscraper
import pytest

# Premier League competition start page — same pattern as ``comps.yaml`` GB1 entry.
TRANSFERMARKT_LEAGUE_URL = (
    "https://www.transfermarkt.us/premier-league/startseite/wettbewerb/GB1"
)


@pytest.mark.contract
def test_transfermarkt_season_select_present() -> None:
    """``get_valid_seasons`` loops until ``soup.find('select', {'name': 'saison_id'})`` succeeds."""
    scraper = cloudscraper.create_scraper()
    response = scraper.get(TRANSFERMARKT_LEAGUE_URL, timeout=60)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "lxml")
    season_select = soup.find("select", {"name": "saison_id"})
    assert season_select is not None, (
        "Season dropdown missing — Transfermarkt HTML layout likely changed."
    )
