"""Tests for tiered Understat ↔ SofaScore merge in unified build."""

from __future__ import annotations

import pandas as pd

from collect_data.build.unified import (
    _apply_name_norm_ss,
    _load_reep_understat_to_sofascore,
    _merge_sofascore_into,
)
from collect_data.config import MANUAL_SS_OVERRIDES
from collect_data.build.unified import _propagate_sofascore_cross_season
from collect_data.helpers import _norm_name


def test_norm_name_decodes_html_entities():
    assert _norm_name("Dara O&#039;Shea") == "dara oshea"


def test_propagate_sofascore_cross_season_from_other_year():
    unified = pd.DataFrame(
        {
            "player": ["Marcin Bulka", "Marcin Bulka"],
            "league": ["France Ligue 1", "France Ligue 1"],
            "season": ["2023-2024", "2024-2025"],
            "understat_id": [7772, 7772],
            "minutes": [3060, 900],
            "sofascore_id": [0, 889590],
            "dribbles_attempted": [0, 5],
            "_name_norm": ["marcin bulka", "marcin bulka"],
        }
    )
    bring = ["dribbles_attempted", "sofascore_rating"]
    n = _propagate_sofascore_cross_season(
        unified, {"France Ligue 1"}, bring
    )
    assert n == 1
    assert int(unified.at[0, "sofascore_id"]) == 889590
    assert float(unified.at[0, "dribbles_attempted"]) == 5.0


def test_manual_ss_override_maps_amad_name():
    unified = pd.DataFrame(
        {
            "player": ["Amad Diallo Traore"],
            "league": ["England Premier League"],
            "season": ["2023-2024"],
        }
    )
    unified["_name_norm"] = unified["player"].apply(_norm_name)
    _apply_name_norm_ss(unified)
    key = ("amad diallo traore", "England Premier League")
    assert key in MANUAL_SS_OVERRIDES
    assert unified.at[0, "_name_norm_ss"] == "amad diallo"


def test_reep_understat_to_sofascore_bridge_loads():
    bridge = _load_reep_understat_to_sofascore()
    # REEP file is optional in CI; when present, Amad's Understat id maps to SofaScore.
    if bridge:
        assert 8127 in bridge or any(v == 971037 for v in bridge.values())


def test_merge_sofascore_fills_by_manual_name_and_reep():
    unified = pd.DataFrame(
        {
            "player": ["Amad Diallo Traore"],
            "team": ["Manchester United"],
            "league": ["England Premier League"],
            "season": ["2023-2024"],
            "understat_id": [8127],
            "minutes": [375],
            "goals": [0],
            "xg": [1.0],
        }
    )
    ss = pd.DataFrame(
        {
            "player": ["Amad Diallo"],
            "team": ["Manchester United"],
            "league": ["England Premier League"],
            "season": ["2023-2024"],
            "sofascore_id": [971037],
            "dribbles_attempted": [21],
            "dribbles_pct": [52.38],
            "sofascore_rating": [6.8],
            "minutes": [388],
        }
    )
    ss["_name_norm"] = ss["player"].apply(_norm_name)
    out = _merge_sofascore_into(unified, ss)
    assert int(out.at[0, "sofascore_id"]) == 971037
    assert float(out.at[0, "dribbles_attempted"]) == 21.0
    assert not bool(out.at[0, "_ss_merge_failed"])
