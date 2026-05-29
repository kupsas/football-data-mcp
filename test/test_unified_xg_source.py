"""Tests for xg_source / xag_source tagging in unified build."""

from __future__ import annotations

import pandas as pd

from collect_data.build.unified import (
    _apply_xg_source_columns,
    _backfill_non_big5_xg_from_sofascore,
)
from collect_data.config import XG_SOURCE_SOFASCORE, XG_SOURCE_UNDERSTAT


def test_xg_source_big5_understat():
    df = pd.DataFrame(
        {
            "player": ["Big Five Striker"],
            "league": ["England Premier League"],
            "season": ["2024-2025"],
            "understat_id": [99],
            "sofascore_id": [1001],
            "xg": [12.0],
            "xag": [4.0],
        }
    )
    _apply_xg_source_columns(df)
    assert df.at[0, "xg_source"] == XG_SOURCE_UNDERSTAT
    assert df.at[0, "xag_source"] == XG_SOURCE_UNDERSTAT


def test_xg_source_eredivisie_sofascore():
    df = pd.DataFrame(
        {
            "player": ["Dutch Winger"],
            "league": ["Netherlands Eredivisie"],
            "season": ["2024-2025"],
            "understat_id": [pd.NA],
            "sofascore_id": [2002],
            "xg": [8.5],
            "xag": [6.0],
        }
    )
    _apply_xg_source_columns(df)
    assert df.at[0, "xg_source"] == XG_SOURCE_SOFASCORE
    assert df.at[0, "xag_source"] == XG_SOURCE_SOFASCORE


def test_backfill_non_big5_xg_from_sofascore():
    unified = pd.DataFrame(
        {
            "league": ["UEFA Champions League"],
            "season": ["2024-2025"],
            "sofascore_id": [42],
            "xg": [0.0],
            "xag": [0.0],
        }
    )
    ss = pd.DataFrame(
        {
            "league": ["UEFA Champions League"],
            "season": ["2024-2025"],
            "sofascore_id": [42],
            "xg": [5.5],
            "xag": [2.1],
        }
    )
    out = _backfill_non_big5_xg_from_sofascore(unified, ss)
    assert float(out.at[0, "xg"]) == 5.5
    assert float(out.at[0, "xag"]) == 2.1
