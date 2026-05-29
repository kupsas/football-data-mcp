"""Tests for per-player season total rows in unified build."""

from __future__ import annotations

import pandas as pd
import pytest

from collect_data.build.unified import (
    _append_season_total_rows,
    _apply_player_identity_columns,
)
from collect_data.config import SEASON_TOTAL_LEAGUE
from collect_data.helpers import sanitize_player_name
def _base_row(**kwargs) -> dict:
    defaults = {
        "player": "Test Player",
        "team": "FC Test",
        "league": "Spain La Liga",
        "season": "2025-2026",
        "minutes": 900,
        "goals": 2,
        "xg": 1.5,
        "npxg": 1.2,
        "sofascore_id": 111,
        "understat_id": 222,
        "sofascore_rating": 7.0,
        "shots_outside_box": 10,
        "is_season_total": False,
    }
    defaults.update(kwargs)
    return defaults


def test_single_competition_row_gets_no_total():
    unified = pd.DataFrame([_base_row()])
    out = _append_season_total_rows(unified)
    assert len(out) == 1
    assert out["is_season_total"].sum() == 0
    assert (out["league"] == SEASON_TOTAL_LEAGUE).sum() == 0


def test_two_competition_rows_get_one_total():
    unified = pd.DataFrame(
        [
            _base_row(
                league="Spain La Liga",
                team="Barcelona",
                minutes=1000,
                goals=3,
                xg=2.0,
                npxg=1.8,
                understat_id=222,
            ),
            _base_row(
                league="UEFA Champions League",
                team="FC Barcelona",
                minutes=400,
                goals=1,
                xg=0.5,
                npxg=0.0,
                understat_id=pd.NA,
                shots_outside_box=4,
            ),
        ]
    )
    out = _append_season_total_rows(unified)
    assert len(out) == 3

    totals = out[out["is_season_total"]]
    assert len(totals) == 1
    row = totals.iloc[0]
    assert row["league"] == SEASON_TOTAL_LEAGUE
    assert row["minutes"] == 1400
    assert row["goals"] == 4
    assert float(row["xg"]) == pytest.approx(2.5)
    assert float(row["npxg"]) == pytest.approx(1.8)
    assert int(row["shots_outside_box"]) == 14
    assert int(row["understat_id"]) == 222
    assert int(row["sofascore_id"]) == 111

    comps = out[~out["is_season_total"]]
    assert len(comps) == 2
    assert set(comps["league"]) == {"Spain La Liga", "UEFA Champions League"}


def test_idempotent_replaces_stale_total_rows():
    unified = pd.DataFrame(
        [
            _base_row(league="Spain La Liga", minutes=500),
            _base_row(
                league="UEFA Champions League",
                team="FC Barcelona",
                minutes=200,
                understat_id=pd.NA,
            ),
            _base_row(
                league=SEASON_TOTAL_LEAGUE,
                minutes=999,
                goals=9,
                is_season_total=True,
            ),
        ]
    )
    out = _append_season_total_rows(unified)
    totals = out[out["is_season_total"]]
    assert len(totals) == 1
    assert float(totals.iloc[0]["minutes"]) == 700
    assert float(totals.iloc[0]["goals"]) == 4


def test_html_entity_names_merge_and_sanitize():
    """O&#039;Shea and O'Shea group to one total with a plain apostrophe display name."""
    unified = pd.DataFrame(
        [
            _base_row(
                player="Dara O&#039;Shea",
                league="England Premier League",
                minutes=800,
                goals=1,
            ),
            _base_row(
                player="Dara O'Shea",
                league="UEFA Champions League",
                team="Ipswich Town",
                minutes=200,
                goals=0,
                understat_id=pd.NA,
            ),
        ]
    )
    out = _append_season_total_rows(unified)
    assert len(out) == 3
    assert out["player"].nunique() == 1
    assert out["player"].iloc[0] == "Dara O'Shea"
    assert out["_name_norm"].notna().all()
    assert (out["_name_norm"] == "dara oshea").all()


def test_apply_player_identity_on_all_rows():
    df = pd.DataFrame([{"player": "Nico O&#039;Reilly", "season": "2024-2025"}])
    _apply_player_identity_columns(df)
    assert df["player"].iloc[0] == "Nico O'Reilly"
    assert df["_name_norm"].iloc[0] == "nico oreilly"


def test_sanitize_player_name():
    assert sanitize_player_name("Dara O&#039;Shea") == "Dara O'Shea"
    assert sanitize_player_name("  Foo   Bar  ") == "Foo Bar"


def test_same_name_different_sofascore_ids_get_separate_totals():
    """Two different players named Aaron Ramsey must not share one season total."""
    unified = pd.DataFrame(
        [
            _base_row(
                player="Aaron Ramsey",
                team="Cardiff City",
                league="England EFL Championship",
                season="2023-2024",
                sofascore_id=23571,
                minutes=722,
                goals=2,
            ),
            _base_row(
                player="Aaron Ramsey",
                team="Burnley",
                league="England Premier League",
                season="2023-2024",
                sofascore_id=1000142,
                minutes=516,
                goals=1,
            ),
        ]
    )
    out = _append_season_total_rows(unified)
    assert len(out) == 2
    assert out["is_season_total"].sum() == 0


def test_transfer_same_sofascore_id_gets_one_total():
    """La Liga + PL + UCL for one SofaScore id (Conor Gallagher pattern)."""
    sid = 904970
    unified = pd.DataFrame(
        [
            _base_row(
                player="Conor Gallagher",
                team="Atletico Madrid",
                league="Spain La Liga",
                sofascore_id=sid,
                minutes=630,
                goals=1,
            ),
            _base_row(
                player="Conor Gallagher",
                team="Tottenham",
                league="England Premier League",
                sofascore_id=sid,
                minutes=962,
                goals=2,
            ),
            _base_row(
                player="Conor Gallagher",
                team="Tottenham Hotspur",
                league="UEFA Champions League",
                sofascore_id=sid,
                minutes=340,
                goals=0,
                understat_id=pd.NA,
            ),
        ]
    )
    out = _append_season_total_rows(unified)
    assert len(out) == 4
    total = out[out["is_season_total"]].iloc[0]
    assert int(total["sofascore_id"]) == sid
    assert total["minutes"] == 1932
    assert total["goals"] == 3


def test_fallback_grouping_when_sofascore_id_missing():
    """Rows with sofascore_id 0 group by _name_norm + season."""
    unified = pd.DataFrame(
        [
            _base_row(
                player="Mystery Player",
                league="Spain La Liga",
                sofascore_id=0,
                minutes=100,
                goals=1,
            ),
            _base_row(
                player="Mystery Player",
                league="UEFA Champions League",
                sofascore_id=pd.NA,
                minutes=50,
                goals=1,
            ),
        ]
    )
    out = _append_season_total_rows(unified)
    assert len(out) == 3
    assert out["is_season_total"].sum() == 1


def test_season_total_recomputes_pct_not_sum():
    """Percentages on All Competitions rows must be recomputed from summed volumes."""
    unified = pd.DataFrame(
        [
            _base_row(
                player="Aaron Bouwman",
                league="Netherlands Eredivisie",
                sofascore_id=999001,
                season="2025-2026",
                ground_duels_won=8,
                ground_duels_won_pct=44.44,
                goals=1,
                shots=10,
                goal_conversion_pct=10.0,
                goals_freekick=1,
                shots_set_piece=2,
                set_piece_conversion=50.0,
                pens_taken=2,
                goals_penalty=1,
                pen_conversion_pct=50.0,
            ),
            _base_row(
                player="Aaron Bouwman",
                league="UEFA Champions League",
                sofascore_id=999001,
                season="2025-2026",
                ground_duels_won=2,
                ground_duels_won_pct=66.67,
                goals=1,
                shots=5,
                goal_conversion_pct=20.0,
                goals_freekick=2,
                shots_set_piece=2,
                set_piece_conversion=100.0,
                pens_taken=1,
                goals_penalty=1,
                pen_conversion_pct=100.0,
                understat_id=pd.NA,
            ),
        ]
    )
    out = _append_season_total_rows(unified)
    total = out[out["is_season_total"]].iloc[0]
    assert float(total["ground_duels_won_pct"]) == pytest.approx(47.62, abs=0.1)
    assert float(total["goal_conversion_pct"]) == pytest.approx(13.33, abs=0.1)
    assert float(total["set_piece_conversion"]) == pytest.approx(75.0, abs=0.1)
    assert float(total["pen_conversion_pct"]) == pytest.approx(66.67, abs=0.1)


def test_rashford_style_grouping_by_sofascore_id():
    """La Liga + UCL rows merge on sofascore_id when understat_id only on domestic row."""
    unified = pd.DataFrame(
        [
            {
                "player": "Marcus Rashford",
                "team": "Barcelona",
                "league": "Spain La Liga",
                "season": "2025-2026",
                "minutes": 100,
                "goals": 1,
                "sofascore_id": 814590,
                "understat_id": 556,
                "is_season_total": False,
            },
            {
                "player": "Marcus Rashford",
                "team": "FC Barcelona",
                "league": "UEFA Champions League",
                "season": "2025-2026",
                "minutes": 50,
                "goals": 2,
                "sofascore_id": 814590,
                "understat_id": pd.NA,
                "is_season_total": False,
            },
        ]
    )
    out = _append_season_total_rows(unified)
    assert out["_name_norm"].notna().all()
    total = out[out["is_season_total"]].iloc[0]
    assert total["minutes"] == 150
    assert total["goals"] == 3
    assert int(total["understat_id"]) == 556


def test_rashford_integration_three_rows_2025_26():
    if not pd.io.common.file_exists("data/unified_player_stats.parquet"):
        pytest.skip("integration: needs built parquet")
    u = pd.read_parquet("data/unified_player_stats.parquet")
    required = {"is_season_total", "_name_norm"}
    if not required.issubset(u.columns):
        pytest.skip(
            "integration: rebuild unified parquet (missing is_season_total / _name_norm)"
        )
    r = u[
        (u["player"].str.contains("Rashford", case=False, na=False))
        & (u["season"] == "2025-2026")
    ]
    assert len(r) == 3
    assert (r["is_season_total"].sum()) == 1
    total = r[r["is_season_total"]].iloc[0]
    comps = r[~r["is_season_total"]]
    assert total["minutes"] == comps["minutes"].sum()
    assert total["league"] == SEASON_TOTAL_LEAGUE
    assert r["_name_norm"].notna().all()
    assert "&#" not in "".join(r["player"].astype(str))
