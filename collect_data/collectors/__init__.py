"""Per-source scrapers (Understat, SofaScore, ClubElo, Transfermarkt, Capology)."""

from __future__ import annotations

from collect_data.collectors.capology import collect_capology
from collect_data.collectors.clubelo import collect_clubelo
from collect_data.collectors.sofascore import collect_sofascore
from collect_data.collectors.sofascore_matches import (
    _sofascore_match_pack_fully_done,
    collect_sofascore_matches,
)
from collect_data.collectors.transfermarkt import collect_transfermarkt
from collect_data.collectors.understat import (
    collect_understat,
    collect_understat_league_tables,
    collect_understat_matches,
)

__all__ = [
    "collect_capology",
    "collect_clubelo",
    "collect_sofascore",
    "collect_sofascore_matches",
    "collect_transfermarkt",
    "collect_understat",
    "collect_understat_league_tables",
    "collect_understat_matches",
    "_sofascore_match_pack_fully_done",
]
