"""
Unit tests for ScraperFC.utils.botasaurus_getters.

These tests use unittest.mock only — no real Chrome, no Bright Data account,
no outbound network. They lock in behaviour of:

  * botasaurus_browser_get_json — Bright Data Web Unlocker path
  * botasaurus_browser_get_json — local headless Chrome path (via a fake @browser)
  * Argument validation (TypeError / ValueError) before any I/O runs
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Same pattern as other tests: import the local src tree before ScraperFC.
sys.path.append("./src/")

from ScraperFC.utils import botasaurus_getters as bg  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers — build a Bright Data client mock that supports `with ... as client`
# ---------------------------------------------------------------------------
def _brightdata_client_factory(result_status: str, result_data):
    """
    Return a MagicMock class that behaves like SyncBrightDataClient when used as::

        with SyncBrightDataClient(...) as client:
            result = client.scrape_url(url, response_format="raw")

    ``result`` is a simple namespace with ``status`` and ``data`` attributes,
    matching what botasaurus_getters._brightdata_get_json expects.
    """
    mock_result = MagicMock(status=result_status, data=result_data)
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = None
    mock_client.scrape_url.return_value = mock_result

    ctor = MagicMock(return_value=mock_client)
    return ctor


def _fake_browser_decorator(page_text: str | None):
    """
    Stand-in for botasaurus.browser.browser.

    The real @browser spins up Chrome.  Here we call the inner scrape function
    with a MagicMock driver whose ``page_text`` we control — exactly what the
    real driver would expose after ``driver.get(url)``.
    """

    def _decorator(fn):
        def _wrapper(url):
            driver = MagicMock()
            driver.page_text = page_text
            return fn(driver, url)

        return _wrapper

    def _browser(**_kwargs):
        return _decorator

    return _browser


# =============================================================================
# Bright Data path (BRIGHTDATA_API_KEY set → _brightdata_get_json)
# =============================================================================
class TestBrightDataPath:
    def test_ready_json_string_parsed(self):
        ctor = _brightdata_client_factory("ready", '{"goals": 2, "assists": 1}')

        with patch.object(bg, "_BRIGHTDATA_KEY", "fake-key-for-test"):
            with patch("brightdata.SyncBrightDataClient", ctor):
                out = bg.botasaurus_browser_get_json("https://api.sofascore.com/api/v1/event/1")

        assert out == {"goals": 2, "assists": 1}
        ctor.return_value.scrape_url.assert_called_once()

    def test_ready_dict_returned_as_is(self):
        payload = {"already": "parsed"}
        ctor = _brightdata_client_factory("ready", payload)

        with patch.object(bg, "_BRIGHTDATA_KEY", "fake-key-for-test"):
            with patch("brightdata.SyncBrightDataClient", ctor):
                out = bg.botasaurus_browser_get_json("https://example.com/x")

        assert out is payload

    def test_ready_empty_body_returns_empty_dict(self):
        ctor = _brightdata_client_factory("ready", "")

        with patch.object(bg, "_BRIGHTDATA_KEY", "fake-key-for-test"):
            with patch("brightdata.SyncBrightDataClient", ctor):
                out = bg.botasaurus_browser_get_json("https://example.com/x")

        assert out == {}

    def test_non_ready_status_returns_empty_dict(self):
        ctor = _brightdata_client_factory("error", '{"should": "not be used"}')

        with patch.object(bg, "_BRIGHTDATA_KEY", "fake-key-for-test"):
            with patch("brightdata.SyncBrightDataClient", ctor):
                out = bg.botasaurus_browser_get_json("https://example.com/x")

        assert out == {}

    def test_ready_invalid_json_returns_empty_dict(self):
        ctor = _brightdata_client_factory("ready", "NOT JSON {{{")

        with patch.object(bg, "_BRIGHTDATA_KEY", "fake-key-for-test"):
            with patch("brightdata.SyncBrightDataClient", ctor):
                out = bg.botasaurus_browser_get_json("https://example.com/x")

        assert out == {}

    def test_delay_positive_calls_sleep_on_bright_path(self):
        ctor = _brightdata_client_factory("ready", "{}")
        with patch.object(bg, "_BRIGHTDATA_KEY", "fake-key-for-test"):
            with patch("brightdata.SyncBrightDataClient", ctor):
                with patch("ScraperFC.utils.botasaurus_getters.time.sleep") as mock_sleep:
                    bg.botasaurus_browser_get_json("https://example.com/x", delay=2)
        mock_sleep.assert_called_once_with(2)


# =============================================================================
# Local Chrome path (no Bright Data key → @browser branch)
# =============================================================================
class TestLocalChromePath:
    def test_non_empty_page_text_json_parsed(self):
        with patch.object(bg, "_BRIGHTDATA_KEY", None):
            with patch.object(bg, "browser", _fake_browser_decorator('{"ok": true}')):
                out = bg.botasaurus_browser_get_json("https://example.com/json")

        assert out == {"ok": True}

    def test_blank_page_text_returns_empty_dict(self):
        with patch.object(bg, "_BRIGHTDATA_KEY", None):
            with patch.object(bg, "browser", _fake_browser_decorator("   ")):
                out = bg.botasaurus_browser_get_json("https://example.com/empty")

        assert out == {}

    def test_none_page_text_returns_empty_dict(self):
        with patch.object(bg, "_BRIGHTDATA_KEY", None):
            with patch.object(bg, "browser", _fake_browser_decorator(None)):
                out = bg.botasaurus_browser_get_json("https://example.com/empty")

        assert out == {}


# =============================================================================
# Validation — must fire before Bright Data or Chrome is touched
# =============================================================================
class TestBotasaurusBrowserGetJsonValidation:
    def test_negative_delay_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            bg.botasaurus_browser_get_json("https://x", delay=-1)

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"url": 123},
            {"headless": "yes"},
            {"block_images_and_css": 1},
            {"wait_for_complete_page_load": 1},
            {"delay": 1.5},
        ],
    )
    def test_wrong_types_raise(self, kwargs):
        base = dict(
            url="https://x",
            headless=True,
            block_images_and_css=True,
            wait_for_complete_page_load=True,
            delay=0,
        )
        base.update(kwargs)
        with pytest.raises(TypeError):
            bg.botasaurus_browser_get_json(**base)
