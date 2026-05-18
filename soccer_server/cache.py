"""Cached access to the unified player table via DuckDB."""

from __future__ import annotations

import logging
import time

import pandas as pd

from soccer_server import db
from soccer_server.data_loading import _parse_age

log = logging.getLogger(__name__)


class DataCache:
    """
    Cached ``unified_prepared`` view from DuckDB.

    With ``ttl_seconds=None`` (default), the first load is kept for the process lifetime.
    With a TTL, the frame is re-read when stale (calls ``db.refresh()`` first).
    """

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl = ttl_seconds
        self._df: pd.DataFrame | None = None
        self._loaded_at: float = 0.0

    def invalidate(self) -> None:
        """Force the next ``get_unified`` to reload from storage."""
        self._df = None
        self._loaded_at = 0.0

    def _is_stale(self) -> bool:
        if self._ttl is None:
            return False
        return (time.time() - self._loaded_at) > float(self._ttl)

    def get_unified(self) -> pd.DataFrame:
        if self._df is not None and not self._is_stale():
            return self._df
        if self._is_stale():
            db.refresh()
        else:
            db.init_db()
        self._df = self._load_and_prepare()
        self._loaded_at = time.time()
        return self._df

    def _load_and_prepare(self) -> pd.DataFrame:
        df = db.query("SELECT * FROM unified_prepared").copy()
        if df.empty:
            log.warning(
                "No unified table found — run ``python -m collect_data`` first."
            )
            return pd.DataFrame()

        id_cols = {
            "player",
            "nation",
            "pos",
            "team",
            "league",
            "season",
            "player_id",
            "team_id",
            "understat_id",
            "tm_id",
            "nationality",
            "citizenship",
            "tm_position",
            "contract_expiration",
            "dob",
            "_player_lower",
            "_team_lower",
        }
        for col in df.columns:
            if col not in id_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        if "minutes" not in df.columns and "minutes_computed" in df.columns:
            df["minutes"] = df["minutes_computed"]

        if "age" in df.columns:
            df["age_num"] = df["age"].apply(_parse_age)
        else:
            df["age_num"] = float("nan")

        log.info(
            "Loaded %s rows × %s cols | %s leagues | %s seasons",
            len(df),
            len(df.columns),
            df["league"].nunique() if "league" in df.columns else 0,
            df["season"].nunique() if "season" in df.columns else 0,
        )
        return df


_default_cache = DataCache()


def get_unified() -> pd.DataFrame:
    """Return the unified player DataFrame (process-wide default cache)."""
    return _default_cache.get_unified()
