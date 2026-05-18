"""Local filesystem storage under ``data/`` (default for open-source users)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


class LocalBackend:
    """
    All paths are relative to the repository ``data/`` directory (DATA_DIR).

    Examples: ``raw/foo.parquet``, ``unified_player_stats.parquet``, ``manifest.json``.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        # Late import avoids circular import while ``collect_data.storage`` is loading.
        from collect_data.storage import DATA_DIR as _default_data

        self.data_dir = (data_dir or _default_data).resolve()

    def _abs(self, rel_path: str) -> Path:
        rel = rel_path.lstrip("/").replace("\\", "/")
        target = (self.data_dir / rel).resolve()
        base = self.data_dir.resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"Path escapes data directory: {rel_path!r}")
        return target

    @property
    def raw_dir(self) -> Path:
        return self._abs("raw")

    def read_parquet_rel(self, rel_path: str) -> pd.DataFrame:
        return pd.read_parquet(self._abs(rel_path))

    def read_csv_rel(self, rel_path: str, **kwargs) -> pd.DataFrame:
        return pd.read_csv(self._abs(rel_path), **kwargs)

    def write_parquet_rel(self, rel_path: str, df: pd.DataFrame) -> str:
        path = self._abs(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
        return str(path)

    def write_csv_rel(self, rel_path: str, df: pd.DataFrame) -> str:
        path = self._abs(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
        return str(path)

    def write_json_rel(self, rel_path: str, data: dict) -> None:
        path = self._abs(rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read_json_rel(self, rel_path: str) -> dict:
        return json.loads(self._abs(rel_path).read_text(encoding="utf-8"))

    def exists_rel(self, rel_path: str) -> bool:
        return self._abs(rel_path).exists()

    def list_raw_glob(self, pattern: str) -> list[str]:
        """Return sorted basenames under ``raw/`` matching ``pattern`` (glob)."""
        rd = self.raw_dir
        if not rd.is_dir():
            return []
        return sorted(p.name for p in rd.glob(pattern))
