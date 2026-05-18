"""Unit tests for :class:`collect_data.storage.CheckpointTracker`."""

from __future__ import annotations

import json
from pathlib import Path

from collect_data.storage import CheckpointTracker


def test_checkpoint_tracker_writes_extended_fields(tmp_path: Path) -> None:
    p = tmp_path / "ckpt.json"
    t = CheckpointTracker("England_Premier_League__2025_2026", p)
    t.write({1, 2, 3}, last_match_date="2026-05-01T12:00:00+00:00", total_finished=380)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["slug"] == "England_Premier_League__2025_2026"
    assert data["done_ids"] == [1, 2, 3]
    assert data["last_match_date"] == "2026-05-01T12:00:00+00:00"
    assert data["total_finished"] == 380
