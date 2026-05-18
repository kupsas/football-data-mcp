"""CLI entry: ``python -m collect_data``."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Match legacy logging: console INFO, file WARNING for SofaScore noise
_LOG_FILE = Path(__file__).resolve().parent.parent / "sofascore_scrape.log"
_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s  %(message)s", datefmt="%H:%M:%S")
)
_file_handler = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
_file_handler.setLevel(logging.WARNING)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s  %(message)s", datefmt="%H:%M:%S")
)
logging.basicConfig(level=logging.INFO, handlers=[_console_handler, _file_handler])
logging.getLogger("websocket").setLevel(logging.CRITICAL)

from collect_data.pipeline import main  # noqa: E402

if __name__ == "__main__":
    main()
