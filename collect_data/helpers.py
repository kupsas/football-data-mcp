"""Small shared utilities used by collectors (browser noise, retries, name normalisation)."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
import re
import unicodedata

log = logging.getLogger(__name__)


@contextlib.contextmanager
def _silence_bota_noise():
    """
    Redirect stdout to /dev/null for the duration of the context.

    botasaurus calls print("Running") before every browser fetch.  Since
    our own logging uses sys.stderr, redirecting stdout is safe and surgically
    removes the noise without touching our progress output.
    """
    with open(os.devnull, "w") as devnull:
        old_stdout, sys.stdout = sys.stdout, devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout


def _norm_name(name: str) -> str:
    """Normalise player name for cross-source matching (accents → ascii, lowercase)."""
    if not isinstance(name, str):
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-z0-9 ]", "", ascii_name.lower())
    return " ".join(clean.split())


def _ss_retry(fn, *args, retries: int = 3, base_sleep: float = 8.0, label: str = "", **kwargs):
    """
    Call fn(*args, **kwargs) up to `retries` times.
    Returns the result on success, or raises the last exception.
    An empty DataFrame / empty dict is treated as a failed attempt so that transient
    botasaurus websocket drops (which return {} instead of raising) get retried too.
    """
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            result = fn(*args, **kwargs)
            empty = (
                (hasattr(result, "empty") and result.empty)
                or (isinstance(result, dict) and not result)
                or (isinstance(result, list) and not result)
            )
            if empty and attempt < retries:
                sleep_s = base_sleep * attempt
                log.warning(
                    f"  ⚠️  {label or fn.__name__} returned empty on attempt {attempt}; "
                    f"retrying in {sleep_s:.0f}s"
                )
                time.sleep(sleep_s)
                continue
            return result
        except Exception as e:
            last_exc = e
            if attempt < retries:
                sleep_s = base_sleep * attempt
                log.warning(
                    f"  ⚠️  {label or fn.__name__} failed attempt {attempt} ({e}); "
                    f"retrying in {sleep_s:.0f}s"
                )
                time.sleep(sleep_s)
    if last_exc is not None:
        raise last_exc
    return fn(*args, **kwargs)
