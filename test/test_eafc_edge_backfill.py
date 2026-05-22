"""Forward anchor backfill: missing seasons from nearest future FIFA match."""

from __future__ import annotations

import pandas as pd

from collect_data.build.eafc import _eafc_forward_anchor_backfill
from collect_data.config import EAFC_UNIFIED_PREFIX


def test_forward_anchor_exact_name_in_gap_season():
    """2023-24 ← 2024-25 anchor when same FIFA name exists in both years."""
    key_col = f"{EAFC_UNIFIED_PREFIX}overall_rating"
    unified = pd.DataFrame(
        {
            "player": ["Transfer Guy", "Transfer Guy"],
            "season": ["2023-2024", "2024-2025"],
            "sofascore_id": [999001.0, 999001.0],
            key_col: [0, 77],
            "eafc_match_player": [None, "T. Guy"],
            f"{EAFC_UNIFIED_PREFIX}club_name": [None, "New FC"],
        }
    )
    eafc_df = pd.DataFrame(
        {
            "eafc_id": [1, 2],
            "season": ["2023-2024", "2024-2025"],
            "player": ["T. Guy", "T. Guy"],
            "club_name": ["Legacy FC", "New FC"],
            "overall_rating": [72, 77],
        }
    )
    out, n = _eafc_forward_anchor_backfill(
        unified, eafc_df, [key_col], key_col
    )
    assert n == 1
    row = out[out.season == "2023-2024"].iloc[0]
    assert row["eafc_match_player"] == "T. Guy"
    assert row[key_col] == 72
    assert row[f"{EAFC_UNIFIED_PREFIX}club_name"] == "Legacy FC"


def test_forward_anchor_eafc_id_when_name_differs():
    """Gap year uses same ``eafc_id`` when display name changed (Dahoud-style)."""
    key_col = f"{EAFC_UNIFIED_PREFIX}overall_rating"
    unified = pd.DataFrame(
        {
            "player": ["Mahmoud Dahoud", "Mahmoud Dahoud"],
            "season": ["2023-2024", "2025-2026"],
            "sofascore_id": [341589.0, 341589.0],
            key_col: [0, 75],
            "eafc_match_player": [None, "Mahmoud Dahoud -"],
            f"{EAFC_UNIFIED_PREFIX}club_name": [None, "Eintracht Frankfurt"],
        }
    )
    eafc_df = pd.DataFrame(
        {
            "eafc_id": [218339, 218339],
            "season": ["2023-2024", "2025-2026"],
            "player": ["M. Dahoud", "Mahmoud Dahoud -"],
            "club_name": ["Borussia Dortmund", "Eintracht Frankfurt"],
            "overall_rating": [80, 75],
        }
    )
    out, n = _eafc_forward_anchor_backfill(
        unified, eafc_df, [key_col], key_col
    )
    assert n == 1
    row = out[out.season == "2023-2024"].iloc[0]
    assert row["eafc_match_player"] == "M. Dahoud"
    assert row[key_col] == 80
    assert row[f"{EAFC_UNIFIED_PREFIX}club_name"] == "Borussia Dortmund"


def test_forward_anchor_cascades_newest_gap_first():
    """2024-25 filled from 2025-26, then 2023-24 can use 2024-25 anchor name."""
    key_col = f"{EAFC_UNIFIED_PREFIX}overall_rating"
    unified = pd.DataFrame(
        {
            "player": ["Cascade", "Cascade", "Cascade"],
            "season": ["2023-2024", "2024-2025", "2025-2026"],
            "sofascore_id": [42.0, 42.0, 42.0],
            key_col: [0, 0, 90],
            "eafc_match_player": [None, None, "C. Player"],
            f"{EAFC_UNIFIED_PREFIX}club_name": [None, None, "FC Future"],
        }
    )
    eafc_df = pd.DataFrame(
        {
            "eafc_id": [10, 10, 10],
            "season": ["2023-2024", "2024-2025", "2025-2026"],
            "player": ["C. Player", "C. Player", "C. Player"],
            "club_name": ["FC Past", "FC Middle", "FC Future"],
            "overall_rating": [70, 80, 90],
        }
    )
    out, n = _eafc_forward_anchor_backfill(
        unified, eafc_df, [key_col], key_col
    )
    assert n == 2
    r24 = out[out.season == "2024-2025"].iloc[0]
    r23 = out[out.season == "2023-2024"].iloc[0]
    assert r24[key_col] == 80
    assert r23[key_col] == 70


def test_forward_anchor_skips_when_no_future_match():
    key_col = f"{EAFC_UNIFIED_PREFIX}overall_rating"
    unified = pd.DataFrame(
        {
            "player": ["Lonely"],
            "season": ["2025-2026"],
            "sofascore_id": [1.0],
            key_col: [0],
            "eafc_match_player": [None],
        }
    )
    out, n = _eafc_forward_anchor_backfill(
        unified,
        pd.DataFrame(
            {
                "eafc_id": [1],
                "season": ["2025-2026"],
                "player": ["Lonely"],
                "overall_rating": [60],
            }
        ),
        [key_col],
        key_col,
    )
    assert n == 0
    assert pd.isna(out["eafc_match_player"].iloc[0])
