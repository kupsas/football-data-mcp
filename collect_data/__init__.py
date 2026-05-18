"""Football stats collection package (scrapers + unified build).

Run the CLI with::

    python -m collect_data

or the compatibility script ``collect_data.py`` in the repository root.
"""

from __future__ import annotations

from collect_data.config import SEASONS
from collect_data.pipeline import (
    _sofascore_match_pack_fully_done,
    build_unified,
    main,
)

__all__ = [
    "SEASONS",
    "_sofascore_match_pack_fully_done",
    "build_unified",
    "main",
]
