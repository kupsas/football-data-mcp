"""Filesystem layout, raw parquet I/O, freshness sidecar, and SofaScore checkpoint writes."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def repo_root() -> Path:
    """Repository root (parent of the ``collect_data`` package directory)."""
    return Path(__file__).resolve().parent.parent


DATA_DIR = repo_root() / "data"
RAW_DIR = DATA_DIR / "raw"
# Sidecar JSON: last fetch time per raw parquet (updated by save_raw).
FRESHNESS_PATH = RAW_DIR / ".freshness.json"


def _update_freshness_record(name: str, row_count: int, path: Path) -> None:
    """Append/update fetch metadata for one raw dataset (best-effort, single-process safe)."""
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        if FRESHNESS_PATH.exists():
            data = json.loads(FRESHNESS_PATH.read_text(encoding="utf-8"))
        else:
            data = {}
        data[name] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rows": int(row_count),
            "path": str(path),
        }
        FRESHNESS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"  Could not update freshness sidecar: {e}")


def raw_freshness_age_hours(dataset_name: str) -> float | None:
    """
    Hours since ``save_raw`` last recorded this dataset in ``.freshness.json``.
    Returns None if unknown.
    """
    if not FRESHNESS_PATH.exists():
        return None
    try:
        data = json.loads(FRESHNESS_PATH.read_text(encoding="utf-8"))
        meta = data.get(dataset_name) or {}
        ts = meta.get("fetched_at")
        if not ts:
            return None
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - t.astimezone(timezone.utc)
        return delta.total_seconds() / 3600.0
    except Exception:
        return None


def save_raw(df: pd.DataFrame, name: str) -> Path:
    """Save a DataFrame as data/raw/<name>.parquet."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{name}.parquet"
    df.to_parquet(path, index=False)
    log.info(f"  💾 {len(df)} rows → {path.name}")
    _update_freshness_record(name, len(df), path)
    return path


class CheckpointTracker:
    """
    Shared checkpoint writer for long incremental jobs (SofaScore match packs).

    Writes JSON alongside parquet flushes so workers can resume after crashes.
    Extended metadata supports weekly incremental runs (``last_match_date``,
    ``total_finished``).
    """

    def __init__(self, slug: str, checkpoint_path: Path) -> None:
        self.slug = slug
        self.checkpoint_path = checkpoint_path

    def write(
        self,
        done_ids: set[int],
        *,
        last_match_date: str | None = None,
        total_finished: int | None = None,
    ) -> None:
        payload: dict = {"slug": self.slug, "done_ids": sorted(done_ids)}
        if last_match_date is not None:
            payload["last_match_date"] = last_match_date
        if total_finished is not None:
            payload["total_finished"] = int(total_finished)
        self.checkpoint_path.write_text(json.dumps(payload), encoding="utf-8")


def sofa_match_checkpoint_flush(
    slug: str,
    done_ids: set[int],
    all_shots: list,
    all_team_rows: list,
    all_play: list,
    all_mom: list,
    p_shots: Path,
    p_team: Path,
    p_play: Path,
    p_mom: Path,
    p_ckpt: Path,
    *,
    last_match_date: str | None = None,
    total_finished: int | None = None,
) -> None:
    """
    Flush accumulated in-memory SofaScore match data to partial parquet files
    and write the checkpoint JSON (crash-resume + incremental metadata).
    """
    try:
        if all_shots:
            pd.concat(all_shots, ignore_index=True).to_parquet(p_shots, index=False)
        if all_team_rows:
            pd.DataFrame(all_team_rows).to_parquet(p_team, index=False)
        if all_play:
            pd.concat(all_play, ignore_index=True).to_parquet(p_play, index=False)
        if all_mom:
            pd.concat(all_mom, ignore_index=True).to_parquet(p_mom, index=False)
        CheckpointTracker(slug, p_ckpt).write(
            done_ids,
            last_match_date=last_match_date,
            total_finished=total_finished,
        )
    except Exception as exc:
        log.warning(f"  ⚠️  Checkpoint flush failed: {exc}")
