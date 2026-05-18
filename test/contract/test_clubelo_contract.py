"""Canary: ClubElo CSV API still returns the columns we load into pandas."""

from __future__ import annotations

from io import StringIO

import pandas as pd
import pytest
import requests

# Single-team history endpoint — small response, stable format.
CLUBELO_TEAM_URL = "http://api.clubelo.com/Barcelona"

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; ScraperFC-canary/1.0; +https://github.com/oseymour/ScraperFC)"
    ),
}


@pytest.mark.contract
def test_clubelo_csv_columns() -> None:
    """Parse CSV text and assert header names match ``ClubElo._clubelo_query`` expectations."""
    response = requests.get(
        CLUBELO_TEAM_URL,
        headers=_DEFAULT_HEADERS,
        timeout=45,
    )
    response.raise_for_status()

    frame = pd.read_csv(StringIO(response.text))
    expected_columns = {"Club", "Elo", "From", "To"}
    assert expected_columns.issubset(set(frame.columns)), (
        f"Missing expected ClubElo columns; got {list(frame.columns)}"
    )
