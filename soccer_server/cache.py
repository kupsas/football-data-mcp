"""In-memory cache for the unified player table with optional TTL (for hosted deployments)."""

from __future__ import annotations

import logging
import time

import pandas as pd

from collect_data.storage import StorageBackend, get_backend
from soccer_server.data_loading import _parse_age

log = logging.getLogger(__name__)


class DataCache:
    """
    Cached unified ``unified_player_stats`` table.

    With ``ttl_seconds=None`` (default), the first load is kept for the process lifetime
    (same behaviour as the original global ``_df``). With a TTL, the frame is re-read from
    the active :class:`~collect_data.storage.StorageBackend` when stale.
    """

    def __init__(
        self,
        backend: StorageBackend | None = None,
        ttl_seconds: int | None = None,
    ) -> None:
        self._backend = backend
        self._ttl = ttl_seconds
        self._df: pd.DataFrame | None = None
        self._loaded_at: float = 0.0

    def _be(self) -> StorageBackend:
        return self._backend if self._backend is not None else get_backend()

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
        self._df = self._load_and_prepare()
        self._loaded_at = time.time()
        return self._df

    def _load_and_prepare(self) -> pd.DataFrame:
        be = self._be()
        if be.exists_rel("unified_player_stats.parquet"):
            df = be.read_parquet_rel("unified_player_stats.parquet")
        elif be.exists_rel("unified_player_stats.csv"):
            df = be.read_csv_rel("unified_player_stats.csv", low_memory=False)
        else:
            log.warning(
                "No unified table found (unified_player_stats.parquet or .csv) — "
                "run ``python -m collect_data`` first."
            )
            return pd.DataFrame()

        id_cols = {
            "player", "nation", "pos", "team", "league", "season",
            "player_id", "team_id", "understat_id", "tm_id",
            "nationality", "citizenship", "tm_position",
            "contract_expiration", "dob",
        }
        for col in df.columns:
            if col not in id_cols:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        df["_player_lower"] = df["player"].astype(str).str.lower()
        df["_team_lower"] = df["team"].astype(str).str.lower()

        if "age" in df.columns:
            df["age_num"] = df["age"].apply(_parse_age)
        else:
            df["age_num"] = float("nan")

        if "minutes" not in df.columns and "ninety_s" in df.columns:
            df["minutes"] = df["ninety_s"] * 90

        log.info(
            "Loaded %s rows × %s cols | %s leagues | %s seasons",
            len(df),
            len(df.columns),
            df["league"].nunique(),
            df["season"].nunique(),
        )
        return df


_default_cache = DataCache()


def get_unified() -> pd.DataFrame:
    """Return the unified player DataFrame (process-wide default cache)."""
    return _default_cache.get_unified()
