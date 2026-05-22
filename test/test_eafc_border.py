"""Tests for promotion/relegation border expansion in EA FC merge."""

from __future__ import annotations

import pandas as pd

from collect_data.build.eafc import (
    FIFA_TOP8_CLUB_LEAGUES,
    _filter_eafc_top8_with_border,
    _top8_club_norms_by_season,
)
from collect_data.helpers import _norm_team


def _row(
    *,
    eafc_id: int,
    season: str,
    player: str,
    club_name: str,
    club_league_name: str,
) -> dict:
    return {
        "eafc_id": eafc_id,
        "season": season,
        "player": player,
        "club_name": club_name,
        "club_league_name": club_league_name,
        "_club_norm": _norm_team(club_name),
        "_name_norm": player.lower(),
        "overall_rating": 70,
    }


def test_border_includes_second_tier_when_club_top8_in_adjacent_season():
    """Darmstadt-style: 2. Bundesliga in X, Bundesliga in X+1 → pool includes X second tier."""
    eafc_df = pd.DataFrame(
        [
            _row(
                eafc_id=1,
                season="2023-2024",
                player="M. Mehlem",
                club_name="Darmstadt 98",
                club_league_name="2. Bundesliga",
            ),
            _row(
                eafc_id=2,
                season="2024-2025",
                player="M. Mehlem",
                club_name="Darmstadt 98",
                club_league_name="Bundesliga",
            ),
        ]
    )
    expanded = _filter_eafc_top8_with_border(eafc_df, seasons=["2023-2024", "2024-2025"])
    assert len(expanded) == 2
    leagues_2324 = set(
        expanded.loc[expanded["season"] == "2023-2024", "club_league_name"]
    )
    assert "2. Bundesliga" in leagues_2324


def test_strict_top8_only_without_adjacent_top8():
    eafc_df = pd.DataFrame(
        [
            _row(
                eafc_id=1,
                season="2023-2024",
                player="X",
                club_name="Obscure FC",
                club_league_name="3. Liga",
            ),
        ]
    )
    expanded = _filter_eafc_top8_with_border(eafc_df, seasons=["2023-2024"])
    assert expanded.empty


def test_top8_club_norms_by_season():
    eafc_df = pd.DataFrame(
        [
            _row(
                eafc_id=1,
                season="2024-2025",
                player="A",
                club_name="Liverpool",
                club_league_name="Premier League",
            ),
        ]
    )
    by_season = _top8_club_norms_by_season(eafc_df)
    assert "2024-2025" in by_season
    assert "liverpool" in by_season["2024-2025"]
    assert all(lg in FIFA_TOP8_CLUB_LEAGUES for lg in ["Premier League"])
