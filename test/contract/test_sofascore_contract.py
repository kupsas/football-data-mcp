"""Canary: SofaScore match JSON still exposes the fields our scraper relies on."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Contract tests live beside ``test/`` but must import the installed package like other tests.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ScraperFC.utils.botasaurus_getters import botasaurus_browser_get_json  # noqa: E402

# Finished historical fixture (Bayern vs Man Utd) — stable ID, unlikely to disappear.
SOFASCORE_EVENT_URL = "https://api.sofascore.com/api/v1/event/11605966"


@pytest.mark.contract
def test_sofascore_event_payload_shape() -> None:
    """One fetch to ``/event/{id}``; assert nested keys/types, not score values.

    Plain ``requests`` / ``cloudscraper`` against ``api.sofascore.com`` often get HTTP 403
    (Akamai / bot rules). The real scraper uses ``botasaurus_browser_get_json`` (headless
    Chrome, or Bright Data when ``BRIGHTDATA_API_KEY`` is set), so the canary uses the same
    transport to avoid false failures while still not importing ``Sofascore`` itself.
    """
    payload = botasaurus_browser_get_json(SOFASCORE_EVENT_URL, headless=True, delay=0)

    assert isinstance(payload, dict) and payload, "Empty or non-JSON response from SofaScore."
    assert "event" in payload, (
        "Top-level JSON must include `event`. "
        f"If keys are {list(payload.keys())}, the upstream response shape changed or the fetch was blocked."
    )
    event = payload["event"]
    assert isinstance(event, dict)

    assert "homeTeam" in event and isinstance(event["homeTeam"], dict)
    assert "name" in event["homeTeam"] and isinstance(event["homeTeam"]["name"], str)

    assert "awayTeam" in event and isinstance(event["awayTeam"], dict)

    assert "homeScore" in event and isinstance(event["homeScore"], dict)
    assert "status" in event and isinstance(event["status"], dict)
