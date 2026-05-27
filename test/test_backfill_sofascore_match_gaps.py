"""Regression tests for backfill parquet merge (no network)."""

import importlib.util
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
_SCRIPT = REPO / "scripts" / "backfill_sofascore_match_gaps.py"


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location("backfill_sofascore_match_gaps", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_merge_parquet_only_replaces_listed_match_ids(tmp_path):
    mod = _load_backfill_module()
    path = tmp_path / "team.parquet"
    pd.DataFrame(
        [
            {"match_id": 1, "key": "xg", "homeValue": 1.0},
            {"match_id": 2, "key": "xg", "homeValue": 2.0},
        ]
    ).to_parquet(path)

    new = pd.DataFrame([{"match_id": 2, "key": "xg", "homeValue": 2.5}])
    out = mod._merge_parquet(path, new, {2})

    assert set(out["match_id"].tolist()) == {1, 2}
    assert out.loc[out["match_id"] == 2, "homeValue"].iloc[0] == 2.5


def test_merge_parquet_does_not_drop_unrelated_ids_when_player_only(tmp_path):
    """Player backfill must not wipe team rows for the same fetched batch."""
    mod = _load_backfill_module()
    path = tmp_path / "team.parquet"
    pd.DataFrame(
        [
            {"match_id": 100, "key": "xg", "homeValue": 1.0},
            {"match_id": 200, "key": "xg", "homeValue": 2.0},
        ]
    ).to_parquet(path)

    # Simulates old bug: player fetched for 200 only, but team merge used {100, 200}.
    player_only_new = pd.DataFrame()  # no team rows for 200
    out = mod._merge_parquet(path, player_only_new, {200})

    assert set(out["match_id"].tolist()) == {100, 200}


def test_match_ids_in_df_empty_when_no_rows():
    mod = _load_backfill_module()
    assert mod._match_ids_in_df(pd.DataFrame()) == set()
    assert mod._match_ids_in_df(pd.DataFrame(columns=["match_id"])) == set()
