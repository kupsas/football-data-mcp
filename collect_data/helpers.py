"""Small shared utilities used by collectors (browser noise, retries, name normalisation)."""

from __future__ import annotations

import contextlib
import html
import logging
import os
import sys
import time
import re
import unicodedata

log = logging.getLogger(__name__)


def season_to_understat_year(season: str) -> str:
    """Map ``'2025-2026'`` → calendar year string Understat APIs use (``'2025'``)."""
    return season.split("-")[0]


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


def sanitize_player_name(name: str) -> str:
    """
    Canonical display name for the unified ``player`` column.

    Understat/SofaScore sometimes emit HTML entities (``O&#039;Shea``). We
  unescape once (twice if still encoded) and collapse whitespace so the same
    person is not split into two display strings in one season.
    """
    if not isinstance(name, str):
        return ""
    text = html.unescape(name.strip())
    if "&#" in text or "&apos;" in text or "&quot;" in text:
        text = html.unescape(text)
    return " ".join(text.split())


def _norm_name(name: str) -> str:
    """Normalise player name for cross-source matching (accents → ascii, lowercase)."""
    if not isinstance(name, str):
        return ""
    name = sanitize_player_name(name)
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-z0-9 ]", "", ascii_name.lower())
    return " ".join(clean.split())


def _primary_team(team: str) -> str:
    """
    SofaScore sometimes lists several clubs in one cell (``AC Milan,Napoli``).

    For matching we use only the first club — the primary / current side in our data.
    """
    if not isinstance(team, str):
        return ""
    s = team.strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return ""
    return s.split(",")[0].strip()


def _norm_team(team: str) -> str:
    """Normalise club/team label for fuzzy matching (accents stripped, lowercase)."""
    if not isinstance(team, str):
        return ""
    s = team.strip()
    if not s or s.lower() in ("nan", "none", "null", "n/a", "free agents", "free agent"):
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    ascii_team = nfkd.encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-z0-9 ]", "", ascii_team.lower())
    return " ".join(clean.split())


def _club_norm_for_match(team: str) -> str:
    """Primary club from a unified ``team`` cell, normalised for EA FC fuzzy merge."""
    return _norm_team(_primary_team(team))


def _team_clubs_norm_list(team: str) -> list[str]:
    """
    All clubs in a SofaScore ``team`` cell (``Osasuna,Real Valladolid`` → two entries).

    Used for multi-pass EA FC fuzzy merge: try the first club, then the second, etc.
    """
    if not isinstance(team, str):
        return []
    s = team.strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in s.split(","):
        norm = _norm_team(part.strip())
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


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
