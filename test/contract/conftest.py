"""Fixtures shared by all contract tests."""

from __future__ import annotations

import time

import pytest

# Monotonic clock value after the previous contract test finished; used to space GETs.
_last_contract_finish: float | None = None


@pytest.fixture(autouse=True)
def _rate_limit_contract_http_calls() -> None:
    """Wait ~2 seconds between live requests so we do not hammer public APIs.

    Pytest may run contract files in any order; this module-level state still
    enforces a minimum gap between successive contract tests in one session.
    """
    global _last_contract_finish
    if _last_contract_finish is not None:
        elapsed = time.monotonic() - _last_contract_finish
        pause = 2.0 - elapsed
        if pause > 0:
            time.sleep(pause)
    yield
    _last_contract_finish = time.monotonic()
