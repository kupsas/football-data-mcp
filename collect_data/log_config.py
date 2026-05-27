"""Shared logging setup for CLI and ProcessPoolExecutor workers."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Repo root: collect_data/log_config.py → parent.parent
_LOG_FILE = Path(__file__).resolve().parent.parent / "sofascore_scrape.log"

_CONFIGURED = False


def setup_collect_logging(
    *,
    console: bool = True,
    log_file: Path | None = _LOG_FILE,
    file_level: int = logging.INFO,
) -> None:
    """
    Configure root logging once per process.

    The main CLI and each ProcessPoolExecutor worker must call this so parallel
  SofaScore jobs emit match progress on stderr (macOS spawn does not inherit
    handlers from the parent).
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    handlers: list[logging.Handler] = []
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s  %(message)s", datefmt="%H:%M:%S"
    )

    if console:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.INFO)
        stderr_handler.setFormatter(fmt)
        handlers.append(stderr_handler)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(file_level)
        file_handler.setFormatter(fmt)
        handlers.append(file_handler)

    logging.basicConfig(level=logging.INFO, handlers=handlers, force=True)
    logging.getLogger("websocket").setLevel(logging.CRITICAL)
    _CONFIGURED = True


def pool_worker_logging_init() -> None:
    """ProcessPoolExecutor initializer: enable INFO logs in child processes."""
    setup_collect_logging()
