"""CLI entry: ``python -m collect_data``."""

from __future__ import annotations

from collect_data.log_config import setup_collect_logging

setup_collect_logging()

from collect_data.pipeline import main  # noqa: E402

if __name__ == "__main__":
    main()
