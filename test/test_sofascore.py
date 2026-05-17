"""
SofaScore scraper tests.

- ``TestSofascore`` — live integration tests (real API + Chrome / Bright Data).
  Run with: ``pytest test/test_sofascore.py -m integration``

- ``TestSofascoreMocked`` — fast offline tests; ``botasaurus_browser_get_json`` is
  patched with canned JSON shaped like the SofaScore API.  These run in CI
  (``tox -e test-sofascore`` uses ``-m "not integration"``).
"""

from __future__ import annotations

import random
import sys
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest
from contextlib import nullcontext as does_not_raise

sys.path.append("./src/")
from ScraperFC import Sofascore  # noqa: E402
from ScraperFC.scraperfc_exceptions import InvalidLeagueException, InvalidYearException  # noqa: E402
from ScraperFC.utils import get_module_comps  # noqa: E402
from ScraperFC.sofascore_player import SofascorePlayer  # noqa: E402

comps = get_module_comps("SOFASCORE")

match_url = (
    "https://www.sofascore.com/fc-bayern-munchen-manchester-united/Ksxdb#id:11605966"
)
match_id = 11605966

# ---------------------------------------------------------------------------
# Offline fixtures — minimal shapes Sofascore methods expect after API fetch
# ---------------------------------------------------------------------------
_MOCK_MATCH_ID = 11605966

# Returned inside {"event": ...} from GET .../event/{id}; get_match_dict strips to inner dict.
_EVENT_INNER = {
    "id": _MOCK_MATCH_ID,
    "customId": "Ksxdb",
    "homeTeam": {
        "name": "FC Bayern München",
        "id": 2672,
        "slug": "fc-bayern-munchen",
    },
    "awayTeam": {
        "name": "Manchester United",
        "id": 35,
        "slug": "manchester-united",
    },
    "homeScore": {"current": 2},
    "awayScore": {"current": 1},
    "status": {"type": "finished"},
}

_MOMENTUM = {
    "graphPoints": [
        {"minute": 1, "value": 3.0},
        {"minute": 2, "value": -1.0},
    ]
}

_TEAM_STATS = {
    "statistics": [
        {
            "period": "ALL",
            "groups": [
                {
                    "groupName": "Attack",
                    "statisticsItems": [
                        {"name": "Shots", "home": "5", "away": "3"},
                    ],
                }
            ],
        }
    ]
}

_LINEUPS = {
    "home": {
        "players": [
            {
                "player": {"id": 10, "name": "Home Striker"},
                "statistics": {"goals": 1, "assists": 0},
            },
        ]
    },
    "away": {
        "players": [
            {
                "player": {"id": 20, "name": "Away Winger"},
                "statistics": {"goals": 0, "assists": 1},
            },
        ]
    },
}


def _fake_browser_json(url: str) -> dict:
    """Route canned responses by URL suffix (same paths as Sofascore class)."""
    mid = _MOCK_MATCH_ID
    if f"/event/{mid}/graph" in url:
        return _MOMENTUM
    if f"/event/{mid}/statistics" in url:
        return _TEAM_STATS
    if f"/event/{mid}/lineups" in url:
        return _LINEUPS
    if url.rstrip("/").endswith(f"/event/{mid}"):
        return {"event": _EVENT_INNER}
    return {}


@pytest.mark.integration
class TestSofascore:
    # ==============================================================================================
    @pytest.mark.parametrize(
        "match, expected",
        [
            (match_url, does_not_raise()),
            (match_id, does_not_raise()),
            (112.0, pytest.raises(TypeError)),
            ((match_url,), pytest.raises(TypeError)),
            ([match_id], pytest.raises(TypeError)),
        ],
    )
    def test_match_url_vs_id(self, match, expected):
        """Test that functions that take in match URLs or match IDs can actually take both."""
        ss = Sofascore()
        with expected:
            ss.get_match_dict(match)
        with expected:
            ss.get_team_names(match)
        with expected:
            ss.get_match_player_ids(match)
        with expected:
            ss.scrape_match_momentum(match)
        with expected:
            ss.scrape_team_match_stats(match)
        with expected:
            ss.scrape_player_match_stats(match)
        with expected:
            ss.scrape_player_average_positions(match)
        with expected:
            ss.scrape_heatmaps(match)

    # ==============================================================================================
    @pytest.mark.parametrize(
        "year, league, expected",
        [
            ("19/20", "France Ligue 1", does_not_raise()),
            ("23/24", "fake league", pytest.raises(InvalidLeagueException)),
            ("17/18", 2000, pytest.raises(TypeError)),
        ],
    )
    def test_invalid_leagues(self, year, league, expected):
        """Test checks on league input"""
        ss = Sofascore()
        with expected:
            ss.get_valid_seasons(league)
        with expected:
            ss.get_match_dicts(year, league)
        with expected:
            ss.scrape_player_league_stats(year, league)
        with expected:
            ss.scrape_team_league_stats(year, league)
        with expected:
            ss.scrape_player_details(year, league)

    # ==============================================================================================
    @pytest.mark.parametrize(
        "year, league, expected",
        [
            ("17/18", "Spain La Liga", does_not_raise()),
            ("fake year", "England Premier League", pytest.raises(InvalidYearException)),
            (2024, "England Premier League", pytest.raises(TypeError)),
        ],
    )
    def test_invalid_years(self, year, league, expected):
        """Test checks on year input"""
        ss = Sofascore()
        with expected:
            ss.get_match_dicts(year, league)
        with expected:
            ss.scrape_player_league_stats(year, league)
        with expected:
            ss.scrape_team_league_stats(year, league)
        with expected:
            ss.scrape_player_details(year, league)

    # ==============================================================================================
    def test_get_match_dicts(self):
        """Test the outputs of the get_match_dicts() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        assert isinstance(match_dicts, list)
        assert np.all([isinstance(x, dict) for x in match_dicts])

    # ==============================================================================================
    def test_get_match_dict(self):
        """Test the outputs of the get_match_dict() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        mid = random.sample(match_dicts, 1)[0]["id"]
        match_dict = ss.get_match_dict(mid)
        assert isinstance(match_dict, dict)

    # ==============================================================================================
    def test_scrape_player_league_stats(self):
        """Test the outputs of the scrape_player_league_stats() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        stats = ss.scrape_player_league_stats(year, league)
        assert isinstance(stats, pd.DataFrame)
        assert ((stats.shape[0] > 0) and (stats.shape[1] > 0)) or (stats.shape == (0, 0))

    # ==============================================================================================
    def test_scrape_match_momentum(self):
        """Test the outputs of the scrape_match_momentum() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        mid = random.sample(match_dicts, 1)[0]["id"]
        momentum = ss.scrape_match_momentum(mid)
        assert isinstance(momentum, pd.DataFrame)
        assert ((momentum.shape[0] > 0) and (momentum.shape[1] > 0)) or (
            momentum.shape == (0, 0)
        )

    # ==============================================================================================
    def test_scrape_team_match_stats(self):
        """Test the outputs of the scrape_team_match_stats() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        mid = random.sample(match_dicts, 1)[0]["id"]
        team_stats = ss.scrape_team_match_stats(mid)
        assert isinstance(team_stats, pd.DataFrame)
        assert ((team_stats.shape[0] > 0) and (team_stats.shape[1] > 0)) or (
            team_stats.shape == (0, 0)
        )

    # ==============================================================================================
    def test_scrape_player_match_stats(self):
        """Test the outputs of the scrape_player_match_stats() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        mid = random.sample(match_dicts, 1)[0]["id"]
        player_stats = ss.scrape_player_match_stats(mid)
        assert isinstance(player_stats, pd.DataFrame)
        assert ((player_stats.shape[0] > 0) and (player_stats.shape[1] > 0)) or (
            player_stats.shape == (0, 0)
        )

    # ==============================================================================================
    def test_scrape_player_average_positions(self):
        """Test the outputs of the scrape_player_average_positions() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        mid = random.sample(match_dicts, 1)[0]["id"]
        avg_pos = ss.scrape_player_average_positions(mid)
        assert isinstance(avg_pos, pd.DataFrame)
        assert ((avg_pos.shape[0] > 0) and (avg_pos.shape[1] > 0)) or (
            avg_pos.shape == (0, 0)
        )

    # ==============================================================================================
    def test_scrape_heatmaps(self):
        """Test the outputs of the scrape_heatmaps() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        mid = random.sample(match_dicts, 1)[0]["id"]
        heatmaps = ss.scrape_heatmaps(mid)
        assert isinstance(heatmaps, dict)
        assert np.all([isinstance(x, dict) for x in heatmaps.values()])
        assert np.all(["id" in x.keys() for x in heatmaps.values()])
        assert np.all([isinstance(x["id"], int) for x in heatmaps.values()])
        assert np.all(["heatmap" in x.keys() for x in heatmaps.values()])
        assert np.all([isinstance(x["heatmap"], list) for x in heatmaps.values()])

    # ==============================================================================================
    def test_scrape_match_shots(self):
        """Test the outputs of the scrape_match_shots() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        match_dicts = ss.get_match_dicts(year, league)
        mid = random.sample(match_dicts, 1)[0]["id"]
        shots = ss.scrape_match_shots(mid)
        assert isinstance(shots, pd.DataFrame)
        assert shots.shape[0] >= 0
        assert shots.shape[1] >= 0

    # ==============================================================================================
    def test_scrape_team_league_stats(self):
        """Test the outputs of the scrape_team_league_stats() function"""
        ss = Sofascore()
        league = random.sample(list(comps.keys()), 1)[0]
        year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]

        team_stats = ss.scrape_team_league_stats(year, league)
        assert isinstance(team_stats, pd.DataFrame)
        assert ((team_stats.shape[0] > 0) and (team_stats.shape[1] > 0)) or (
            team_stats.shape == (0, 0)
        )

    # ==============================================================================================
    def test_scrape_player_details(self):
        ss = Sofascore()
        while 1:
            league = random.sample(list(comps.keys()), 1)[0]
            year = random.sample(list(ss.get_valid_seasons(league).keys()), 1)[0]
            player_ids = ss.get_league_player_ids(year, league)
            if len(player_ids) > 0:
                break

        player_details = ss.scrape_player_details(year, league)
        assert len(player_ids) == len(player_details)
        assert isinstance(player_details, list)
        assert all(isinstance(player, SofascorePlayer) for player in player_details)


class TestSofascoreMocked:
    """Fast tests with ``botasaurus_browser_get_json`` patched — no browser, no network."""

    @patch("ScraperFC.sofascore.botasaurus_browser_get_json", side_effect=_fake_browser_json)
    def test_get_match_dict_returns_event_payload(self, _mock):
        ss = Sofascore()
        out = ss.get_match_dict(_MOCK_MATCH_ID)
        assert isinstance(out, dict)
        assert out["id"] == _MOCK_MATCH_ID
        assert out["homeTeam"]["name"] == "FC Bayern München"

    @patch("ScraperFC.sofascore.botasaurus_browser_get_json", side_effect=_fake_browser_json)
    def test_get_team_names(self, _mock):
        ss = Sofascore()
        home, away = ss.get_team_names(_MOCK_MATCH_ID)
        assert home == "FC Bayern München"
        assert away == "Manchester United"

    @patch("ScraperFC.sofascore.botasaurus_browser_get_json", side_effect=_fake_browser_json)
    def test_scrape_match_momentum_shape(self, _mock):
        ss = Sofascore()
        df = ss.scrape_match_momentum(_MOCK_MATCH_ID)
        assert isinstance(df, pd.DataFrame)
        assert list(df.columns) == ["minute", "value"]
        assert len(df) == 2

    @patch("ScraperFC.sofascore.botasaurus_browser_get_json", side_effect=_fake_browser_json)
    def test_scrape_team_match_stats_shape(self, _mock):
        ss = Sofascore()
        df = ss.scrape_team_match_stats(_MOCK_MATCH_ID)
        assert isinstance(df, pd.DataFrame)
        assert "period" in df.columns and "group" in df.columns
        assert df.shape[0] >= 1

    @patch("ScraperFC.sofascore.botasaurus_browser_get_json", side_effect=_fake_browser_json)
    def test_scrape_player_match_stats_shape(self, _mock):
        ss = Sofascore()
        df = ss.scrape_player_match_stats(_MOCK_MATCH_ID)
        assert isinstance(df, pd.DataFrame)
        assert df.shape[0] == 2  # one home + one away stub player

    def test_invalid_match_type_raises_before_network(self):
        """Type check in ``_check_and_convert_match_id`` — no HTTP / browser call."""
        ss = Sofascore()
        with pytest.raises(TypeError, match="string or int"):
            ss.get_match_dict(112.0)
