"""Filesystem layout, storage backends, raw parquet I/O, and SofaScore checkpoint writes."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, runtime_checkable

import pandas as pd

log = logging.getLogger(__name__)


def repo_root() -> Path:
    """Repository root (parent of the ``collect_data`` package directory)."""
    return Path(__file__).resolve().parent.parent


# ── Canonical paths (single source of truth) ──────────────────────────────────

DATA_DIR = repo_root() / "data"
RAW_DIR = DATA_DIR / "raw"
FRESHNESS_PATH = RAW_DIR / ".freshness.json"
UNIFIED_PARQUET = DATA_DIR / "unified_player_stats.parquet"
UNIFIED_CSV = DATA_DIR / "unified_player_stats.csv"
MANIFEST_PATH = DATA_DIR / "manifest.json"


@runtime_checkable
class StorageBackend(Protocol):
    """Abstraction over local disk vs S3-compatible object storage (e.g. R2)."""

    def read_parquet_rel(self, rel_path: str) -> pd.DataFrame:
        """Read a Parquet file relative to ``DATA_DIR`` (e.g. ``raw/foo.parquet``)."""
        ...

    def read_csv_rel(self, rel_path: str, **kwargs) -> pd.DataFrame:
        """Read a CSV file relative to ``DATA_DIR``."""
        ...

    def write_parquet_rel(self, rel_path: str, df: pd.DataFrame) -> str:
        """Write a Parquet file; returns a URI or filesystem path for logging."""
        ...

    def write_csv_rel(self, rel_path: str, df: pd.DataFrame) -> str:
        """Write a CSV file."""
        ...

    def write_json_rel(self, rel_path: str, data: dict) -> None:
        """Write JSON (UTF-8)."""
        ...

    def read_json_rel(self, rel_path: str) -> dict:
        """Read JSON object."""
        ...

    def exists_rel(self, rel_path: str) -> bool:
        """Whether the object exists."""
        ...

    def list_raw_glob(self, pattern: str) -> list[str]:
        """Sorted basenames under ``raw/`` matching the glob ``pattern``."""
        ...


_backend: StorageBackend | None = None


def get_backend() -> StorageBackend:
    """Active storage backend from ``DATA_BACKEND`` (``local`` default, ``r2`` for R2)."""
    global _backend
    if _backend is not None:
        return _backend

    mode = os.getenv("DATA_BACKEND", "local").lower().strip()
    if mode == "r2":
        from collect_data.backends.r2 import R2Backend

        _backend = R2Backend()
    else:
        from collect_data.backends.local import LocalBackend

        _backend = LocalBackend()
    return _backend


def reset_backend() -> None:
    """Clear cached backend (for tests or after env change)."""
    global _backend
    _backend = None


def _update_freshness_record(name: str, row_count: int, path: str) -> None:
    """Append/update fetch metadata for one raw dataset (best-effort, single-process safe)."""
    try:
        be = get_backend()
        rel = "raw/.freshness.json"
        if be.exists_rel(rel):
            data = be.read_json_rel(rel)
        else:
            data = {}
        data[name] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rows": int(row_count),
            "path": path,
        }
        be.write_json_rel(rel, data)
    except Exception as e:
        log.warning(f"  Could not update freshness sidecar: {e}")


def raw_freshness_age_hours(dataset_name: str) -> float | None:
    """
    Hours since ``save_raw`` last recorded this dataset in ``.freshness.json``.
    Returns None if unknown.
    """
    be = get_backend()
    rel = "raw/.freshness.json"
    if not be.exists_rel(rel):
        return None
    try:
        data = be.read_json_rel(rel)
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
    """Save a DataFrame as ``data/raw/<name>.parquet`` (or R2 key ``raw/<name>.parquet``)."""
    rel = f"raw/{name}.parquet"
    uri = get_backend().write_parquet_rel(rel, df)
    log.info(f"  💾 {len(df)} rows → {name}.parquet")
    _update_freshness_record(name, len(df), uri)
    # Callers expect a Path under ``RAW_DIR``; keep that contract for local workflows.
    return RAW_DIR / f"{name}.parquet"


def load_parquets(pattern: str, backend: StorageBackend | None = None) -> pd.DataFrame:
    """Load and concatenate all ``raw/*.parquet`` files whose basename matches ``pattern``."""
    be = backend or get_backend()
    frames: list[pd.DataFrame] = []
    for fname in be.list_raw_glob(pattern):
        try:
            frames.append(be.read_parquet_rel(f"raw/{fname}"))
        except Exception as e:
            log.warning(f"Could not load {fname}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def freshness_summary(backend: StorageBackend | None = None) -> dict:
    """Summarise ``raw/.freshness.json`` for MCP ``data_status``."""
    be = backend or get_backend()
    out: dict = {"freshness_entries": 0}
    rel = "raw/.freshness.json"
    if not be.exists_rel(rel):
        return out
    try:
        data = be.read_json_rel(rel)
        out["freshness_entries"] = len(data)
        times = []
        for meta in data.values():
            ts = meta.get("fetched_at")
            if not ts:
                continue
            try:
                times.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
            except ValueError:
                continue
        if times:
            oldest = min(times)
            newest = max(times)
            out["oldest_raw_fetch_utc"] = oldest.isoformat()
            out["newest_raw_fetch_utc"] = newest.isoformat()
            age_days = (datetime.now(timezone.utc) - newest).total_seconds() / 86400
            out["newest_raw_fetch_age_days"] = round(age_days, 2)
    except Exception as e:
        out["freshness_error"] = str(e)
    return out


def manifest_summary(backend: StorageBackend | None = None) -> dict:
    """Read ``manifest.json`` for build timestamps."""
    be = backend or get_backend()
    rel = "manifest.json"
    if not be.exists_rel(rel):
        return {}
    try:
        m = be.read_json_rel(rel)
        return {
            k: m[k]
            for k in ("last_built_at", "oldest_source_fetched_at")
            if k in m and m[k] is not None
        }
    except Exception as e:
        return {"manifest_error": str(e)}


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
