"""
Regression tests for SofaScore match-pack "fully done" detection in collect_data.

Mid-run checkpoint flushes write all four parquet files; the collector must not
treat that state as a finished season.
"""

from __future__ import annotations

from pathlib import Path

import collect_data as cd


def _touch(p: Path) -> None:
    p.write_bytes(b"")


def test_pack_not_done_when_checkpoint_exists(tmp_path: Path) -> None:
    p_shots = tmp_path / "sofascore_match_shots__X.parquet"
    p_team = tmp_path / "sofascore_match_team_stats__X.parquet"
    p_play = tmp_path / "sofascore_match_player_stats__X.parquet"
    p_mom = tmp_path / "sofascore_match_momentum__X.parquet"
    p_ckpt = tmp_path / "sofascore_match_checkpoint__X.json"
    for p in (p_shots, p_team, p_play, p_mom):
        _touch(p)
    p_ckpt.write_text('{"done_ids": [1, 2, 3]}', encoding="utf-8")

    assert not cd._sofascore_match_pack_fully_done(
        False, p_shots, p_team, p_play, p_mom, p_ckpt
    )


def test_pack_done_when_four_parquets_and_no_checkpoint(tmp_path: Path) -> None:
    p_shots = tmp_path / "sofascore_match_shots__X.parquet"
    p_team = tmp_path / "sofascore_match_team_stats__X.parquet"
    p_play = tmp_path / "sofascore_match_player_stats__X.parquet"
    p_mom = tmp_path / "sofascore_match_momentum__X.parquet"
    p_ckpt = tmp_path / "sofascore_match_checkpoint__X.json"
    for p in (p_shots, p_team, p_play, p_mom):
        _touch(p)

    assert cd._sofascore_match_pack_fully_done(
        False, p_shots, p_team, p_play, p_mom, p_ckpt
    )


def test_force_never_considers_pack_done(tmp_path: Path) -> None:
    p_shots = tmp_path / "sofascore_match_shots__X.parquet"
    p_team = tmp_path / "sofascore_match_team_stats__X.parquet"
    p_play = tmp_path / "sofascore_match_player_stats__X.parquet"
    p_mom = tmp_path / "sofascore_match_momentum__X.parquet"
    p_ckpt = tmp_path / "sofascore_match_checkpoint__X.json"
    for p in (p_shots, p_team, p_play, p_mom):
        _touch(p)

    assert not cd._sofascore_match_pack_fully_done(
        True, p_shots, p_team, p_play, p_mom, p_ckpt
    )
