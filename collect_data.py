#!/usr/bin/env python3
"""
Football Data Collector — FBref + Understat + SofaScore

Sources:
  FBref      (via ScraperFC / botasaurus Chrome) — basic stats for 8 leagues
  Understat  (direct POST API, no browser)       — xG/xA for Big 5 leagues
  SofaScore  (via ScraperFC / botasaurus Chrome) — 80+ stats for 37 leagues/comps

Usage:
  python3 collect_data.py                        # all sources + SofaScore matches + ClubElo + unified CSV
  python3 collect_data.py --sofascore-only       # SofaScore season stats only (no per-match pack)
  python3 collect_data.py --sofascore-matches-only  # SofaScore per-match parquets only
  python3 collect_data.py --sofascore-matches-only --parallel 5  # same, up to 5 league-seasons at once
  python3 collect_data.py --clubelo-only         # ClubElo snapshot + fixtures only
  python3 collect_data.py --no-sofascore-matches # Full run but skip long per-match SofaScore step
  python3 collect_data.py --no-clubelo           # Full run without ClubElo
  python3 collect_data.py --rebuild-only         # skip scraping, rebuild CSV from raw files
  python3 collect_data.py --seasons 2025-2026 --leagues "England Premier League"
"""

import argparse
import concurrent.futures
import json
import logging
import re
import sys
import time
import unicodedata
import random
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pandas as pd
import requests

_LOG_FILE = Path(__file__).parent / "sofascore_scrape.log"

# Console: INFO and above — clean match-by-match progress
_console_handler = logging.StreamHandler(sys.stderr)
_console_handler.setLevel(logging.INFO)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s  %(message)s", datefmt="%H:%M:%S")
)

# File: WARNING and above — retry noise, errors, skipped matches
_file_handler = logging.FileHandler(_LOG_FILE, mode="a", encoding="utf-8")
_file_handler.setLevel(logging.WARNING)
_file_handler.setFormatter(
    logging.Formatter("%(asctime)s %(levelname)s  %(message)s", datefmt="%H:%M:%S")
)

logging.basicConfig(
    level=logging.INFO,
    handlers=[_console_handler, _file_handler],
)
log = logging.getLogger(__name__)

# ── Silence botasaurus/websocket noise ─────────────────────────────────────────
# websocket-client logs "Websocket connected" (INFO) and
# "Connection to remote host was lost. - goodbye" (ERROR) on every single
# botasaurus Chrome call.  They go through the "websocket" logger.
# These are cosmetic CDP lifecycle messages — not errors in our scraper.
logging.getLogger("websocket").setLevel(logging.CRITICAL)


import contextlib as _contextlib
import os as _os


@_contextlib.contextmanager
def _silence_bota_noise():
    """
    Redirect stdout to /dev/null for the duration of the context.

    botasaurus calls print("Running") before every browser fetch.  Since
    our own logging uses sys.stderr, redirecting stdout is safe and surgically
    removes the noise without touching our progress output.
    """
    with open(_os.devnull, "w") as _devnull:
        old_stdout, sys.stdout = sys.stdout, _devnull
        try:
            yield
        finally:
            sys.stdout = old_stdout

DATA_DIR = Path(__file__).parent / "data"
RAW_DIR  = DATA_DIR / "raw"
# Sidecar JSON: last fetch time per raw parquet (updated by save_raw).
FRESHNESS_PATH = RAW_DIR / ".freshness.json"


# ══════════════════════════════════════════════════════════════════════════════
#  FBref configuration
# ══════════════════════════════════════════════════════════════════════════════

FBREF_LEAGUES = [
    "England Premier League",
    "Spain La Liga",
    "Germany Bundesliga",
    "Italy Serie A",
    "France Ligue 1",
    "Netherlands Eredivisie",
    "Portugal Primeira Liga",
    "Belgium Pro League",
]

SEASONS = ["2025-2026", "2024-2025", "2023-2024"]

STAT_CATEGORIES = [
    "standard", "shooting", "passing", "pass types",
    "goal and shot creation", "defensive", "possession",
    "playing time", "misc", "goalkeeping", "advanced goalkeeping",
]

# Rename FBref leaf column name → our canonical snake_case name
FBREF_COL_RENAME = {
    "Player": "player", "Nation": "nation", "Pos": "pos", "Squad": "team",
    "Age": "age", "Born": "born",
    "MP": "games", "Starts": "starts", "Min": "minutes", "90s": "ninety_s",
    "Gls": "goals", "Ast": "assists", "G+A": "goals_assists",
    "G-PK": "goals_non_pen", "PK": "pens_scored", "PKatt": "pens_att",
    "CrdY": "yellow_cards", "CrdR": "red_cards",
    "xG": "xg_fbref", "npxG": "npxg_fbref", "xAG": "xag_fbref",
    "Sh": "shots", "SoT": "shots_on_target", "SoT%": "shot_on_target_pct",
    "G/Sh": "goals_per_shot", "Dist": "shot_distance",
    "Cmp": "passes_completed", "Att": "passes_attempted",
    "Cmp%": "pass_completion_pct", "PrgDist": "progressive_pass_dist",
    "KP": "key_passes", "1/3": "passes_final_third",
    "PPA": "passes_penalty_area", "CrsPA": "crosses_penalty_area",
    "PrgP": "progressive_passes",
    "SCA": "sca", "SCA90": "sca_per90", "GCA": "gca", "GCA90": "gca_per90",
    "Tkl": "tackles_fbref", "TklW": "tackles_won",
    "Def 3rd": "tackles_def_3rd", "Mid 3rd": "tackles_mid_3rd",
    "Att 3rd": "tackles_att_3rd",
    "Int": "interceptions", "Tkl+Int": "tackles_interceptions",
    "Clr": "clearances", "Err": "errors", "Blocks": "blocks",
    "Touches": "touches", "Carries": "carries",
    "PrgC": "progressive_carries",
    "Rec": "passes_received", "PrgR": "progressive_passes_received",
    "Fls": "fouls", "Fld": "fouled", "Off": "offsides", "Crs": "crosses",
    "Won": "aerials_won", "Lost": "aerials_lost", "Won%": "aerials_won_pct",
    "GA": "goals_against", "GA90": "goals_against_per90",
    "SoTA": "shots_on_target_against", "Saves": "saves",
    "Save%": "save_pct", "CS": "clean_sheets", "CS%": "clean_sheet_pct",
    "PKsv": "pens_saved",
    "Player ID": "player_id", "Team ID": "team_id",
}


# ══════════════════════════════════════════════════════════════════════════════
#  Understat configuration
# ══════════════════════════════════════════════════════════════════════════════

UNDERSTAT_LEAGUES = {
    "England Premier League": "EPL",
    "Spain La Liga":          "La_liga",
    "Germany Bundesliga":     "Bundesliga",
    "Italy Serie A":          "Serie_A",
    "France Ligue 1":         "Ligue_1",
}

UNDERSTAT_API = "https://understat.com/main/getPlayersStats/"
UNDERSTAT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://understat.com/",
}

# Understat AJAX endpoints (all data now served via these, not embedded HTML)
UNDERSTAT_AJAX_BASE = "https://understat.com/"
UNDERSTAT_AJAX_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}


# ══════════════════════════════════════════════════════════════════════════════
#  SofaScore configuration
# ══════════════════════════════════════════════════════════════════════════════

# Target leagues for SofaScore collection
SOFASCORE_TARGET_LEAGUES = [
    "England Premier League",
    "Spain La Liga",
    "Germany Bundesliga",
    "Italy Serie A",
    "France Ligue 1",
    "Netherlands Eredivisie",
    "Portugal Primeira Liga",
    "UEFA Champions League",
    "UEFA Europa League",
    "England EFL Championship",
]

# Maps our YYYY-YYYY season string → SofaScore YY/YY format
SOFASCORE_SEASONS = {
    "2025-2026": "25/26",
    "2024-2025": "24/25",
    "2023-2024": "23/24",
}

# SofaScore camelCase field → our snake_case column name
SS_COL_RENAME = {
    # identifiers
    "player id":               "sofascore_id",
    "team id":                 "sofascore_team_id",
    # rating
    "rating":                  "sofascore_rating",
    "totalRating":             "sofascore_rating_total",
    "countRating":             "sofascore_rating_count",
    "totwAppearances":         "totw_appearances",
    # appearances / time
    "appearances":             "games",
    "matchesStarted":          "starts",
    "minutesPlayed":           "minutes",
    # attacking
    "goals":                   "goals",
    "assists":                 "assists",
    "goalsAssistsSum":         "goals_assists",
    "ownGoals":                "own_goals",
    "goalsFromInsideTheBox":   "goals_inside_box",
    "goalsFromOutsideTheBox":  "goals_outside_box",
    "headedGoals":             "goals_headed",
    "leftFootGoals":           "goals_left_foot",
    "rightFootGoals":          "goals_right_foot",
    "penaltyGoals":            "goals_penalty",
    "freeKickGoal":            "goals_freekick",
    "hitWoodwork":             "hit_woodwork",
    "bigChancesMissed":        "big_chances_missed",
    "scoringFrequency":        "scoring_frequency",
    "goalConversionPercentage":"goal_conversion_pct",
    "setPieceConversion":      "set_piece_conversion",
    # xG (SofaScore model — we prefer Understat for Big5, but keep for other leagues)
    "expectedGoals":           "xg",
    "expectedAssists":         "xag",
    # shooting
    "totalShots":              "shots",
    "shotsOnTarget":           "shots_on_target",
    "shotsOffTarget":          "shots_off_target",
    "shotsFromInsideTheBox":   "shots_inside_box",
    "shotsFromOutsideTheBox":  "shots_outside_box",
    "blockedShots":            "blocked_shots",
    "outfielderBlocks":        "outfield_blocks",
    "shotFromSetPiece":        "shots_set_piece",
    # passing
    "totalPasses":             "passes_total",
    "accuratePasses":          "passes_completed",
    "inaccuratePasses":        "passes_inaccurate",
    "accuratePassesPercentage":"pass_completion_pct",
    "accurateFinalThirdPasses":"passes_final_third",
    "totalOppositionHalfPasses":"passes_opp_half_total",
    "accurateOppositionHalfPasses":"passes_opp_half",
    "totalOwnHalfPasses":      "passes_own_half_total",
    "accurateOwnHalfPasses":   "passes_own_half",
    "totalLongBalls":          "long_balls_total",
    "accurateLongBalls":       "long_balls_completed",
    "accurateLongBallsPercentage":"long_balls_pct",
    "totalCross":              "crosses_total",
    "accurateCrosses":         "crosses_completed",
    "accurateCrossesPercentage":"crosses_pct",
    "totalChippedPasses":      "chipped_passes_total",
    "accurateChippedPasses":   "chipped_passes_completed",
    # creation
    "keyPasses":               "key_passes",
    "passToAssist":            "pass_to_assist",
    "totalAttemptAssist":      "attempt_assists",
    "bigChancesCreated":       "big_chances_created",
    # defensive
    "tackles":                 "tackles",
    "tacklesWon":              "tackles_won",
    "tacklesWonPercentage":    "tackles_won_pct",
    "interceptions":           "interceptions",
    "clearances":              "clearances",
    "errorLeadToGoal":         "errors_leading_to_goal",
    "errorLeadToShot":         "errors_leading_to_shot",
    "dribbledPast":            "dribbled_past",
    # aerial
    "aerialDuelsWon":          "aerials_won",
    "aerialDuelsWonPercentage":"aerials_won_pct",
    "aerialLost":              "aerials_lost",
    # dribbling / duels
    "successfulDribbles":      "dribbles_completed",
    "successfulDribblesPercentage":"dribbles_pct",
    "totalContest":            "dribbles_attempted",
    "groundDuelsWon":          "ground_duels_won",
    "groundDuelsWonPercentage":"ground_duels_won_pct",
    "totalDuelsWon":           "duels_won",
    "totalDuelsWonPercentage": "duels_won_pct",
    "duelLost":                "duels_lost",
    # possession / pressing
    "touches":                 "touches",
    "possessionLost":          "possession_lost",
    "possessionWonAttThird":   "possession_won_att_third",
    "dispossessed":            "dispossessed",
    "ballRecovery":            "ball_recoveries",
    # fouls / discipline
    "fouls":                   "fouls",
    "wasFouled":               "fouled",
    "offsides":                "offsides",
    "yellowCards":             "yellow_cards",
    "yellowRedCards":          "yellow_red_cards",
    "redCards":                "red_cards",
    "directRedCards":          "direct_red_cards",
    # penalty (outfield)
    "penaltiesTaken":          "pens_taken",
    "penaltyConceded":         "pens_conceded",
    "penaltyWon":              "pens_won",
    "penaltyConversion":       "pen_conversion_pct",
    "attemptPenaltyMiss":      "pen_miss",
    "attemptPenaltyPost":      "pen_post",
    "attemptPenaltyTarget":    "pen_on_target",
    # goalkeeper
    "saves":                   "saves",
    "savesCaught":             "saves_caught",
    "savesParried":            "saves_parried",
    "savedShotsFromInsideTheBox": "saves_inside_box",
    "savedShotsFromOutsideTheBox":"saves_outside_box",
    "goalsConceded":           "goals_conceded",
    "goalsConcededInsideTheBox":"goals_conceded_inside_box",
    "goalsConcededOutsideTheBox":"goals_conceded_outside_box",
    "goalsPrevented":          "goals_prevented",
    "cleanSheet":              "clean_sheets",
    "highClaims":              "high_claims",
    "crossesNotClaimed":       "crosses_not_claimed",
    "punches":                 "punches",
    "runsOut":                 "runs_out",
    "successfulRunsOut":       "runs_out_successful",
    "goalKicks":               "goal_kicks",
    "penaltySave":             "pens_saved",
    "penaltyFaced":            "pens_faced",
}

# Stats already authoritative from Understat — don't overwrite from SofaScore for Big5 leagues
UNDERSTAT_AUTHORITATIVE = frozenset({
    "goals", "assists", "shots", "minutes", "games",
    "yellow_cards", "red_cards", "key_passes",
    "xg", "xag", "npxg", "npg", "xg_chain", "xg_buildup",
})


# ══════════════════════════════════════════════════════════════════════════════
#  Manual TM name overrides
#  Keys: (norm(unified_player_name), league)  →  norm(tm_name)
#  Used when the player is known by a different name/nickname on each platform.
# ══════════════════════════════════════════════════════════════════════════════

MANUAL_TM_OVERRIDES: dict[tuple[str, str], str] = {
    # ── England EFL Championship ─────────────────────────────────────────────
    ("vinicius souza",        "England EFL Championship"): "vini souza",
    ("frederick issaka",      "England EFL Championship"): "freddie issaka",
    ("solomon brynn",         "England EFL Championship"): "sol brynn",
    ("alexander gilbert",     "England EFL Championship"): "alex gilbert",
    # ── England Premier League ───────────────────────────────────────────────
    ("kepa",                  "England Premier League"):   "kepa arrizabalaga",
    ("matthew cash",          "England Premier League"):   "matty cash",
    ("savio",                 "England Premier League"):   "savinho",
    ("chimuanya ugochukwu",   "England Premier League"):   "lesley ugochukwu",   # full: Lesley Chimuanya Ugochukwu
    ("jaden philogenebidace", "England Premier League"):   "jaden philogene",
    ("andy irving",           "England Premier League"):   "andrew irving",
    # ── France Ligue 1 ──────────────────────────────────────────────────────
    ("mat ryan",              "France Ligue 1"):           "mathew ryan",
    ("mathis cherki",         "France Ligue 1"):           "rayan cherki",       # SofaScore wrong first name
    ("tinotenda kadewere",    "France Ligue 1"):           "tino kadewere",
    ("hianga039a m039bock",   "France Ligue 1"):           "hiangaa mbock",      # HTML entity artifact
    ("zabi gueu",             "France Ligue 1"):           "patrick zabi",       # TM uses reversed name order
    ("john finn",             "France Ligue 1"):           "john patrick",       # TM: "John Patrick" at Reims
    # ── Germany Bundesliga ──────────────────────────────────────────────────
    ("alex grimaldo",         "Germany Bundesliga"):       "alejandro grimaldo",
    ("bote baku",             "Germany Bundesliga"):       "ridle baku",         # full: Bote Baku Ridle
    ("julian chabot",         "Germany Bundesliga"):       "jeff chabot",        # TM uses middle name Jeff
    ("jamie bynoegittens",    "Germany Bundesliga"):       "jamie gittens",
    ("jannfiete arp",         "Germany Bundesliga"):       "fiete arp",
    # ── Italy Serie A ───────────────────────────────────────────────────────
    ("franck zambo",          "Italy Serie A"):            "frank anguissa",     # André-Frank Zambo Anguissa
    ("ndary adopo",           "Italy Serie A"):            "michel adopo",       # Ndary Michel Adopo
    ("valentin castellanos",  "Italy Serie A"):            "taty castellanos",   # Taty = nickname for Valentín
    ("zito",                  "Italy Serie A"):            "zito luvumbo",
    ("alejandro jimenez",     "Italy Serie A"):            "alex jimenez",
    ("keita",                 "Italy Serie A"):            "keita balde",        # Keita Baldé Diao at Monza
    ("tasos douvikas",        "Italy Serie A"):            "anastasios douvikas",
    ("kouadio kone",          "Italy Serie A"):            "manu kone",          # Emmanuel Kouadio Koné → Manu Koné
    # ── Portugal Primeira Liga ───────────────────────────────────────────────
    ("jason",                 "Portugal Primeira Liga"):   "jason remeseiro",
    ("dudu",                  "Portugal Primeira Liga"):   "dudu teodora",
    ("fahem benaissayahia",   "Portugal Primeira Liga"):   "fahem benaissa",
    ("francisco goncalves",   "Portugal Primeira Liga"):   "chico goncalves",    # Chico = Francisco
    # ── Spain La Liga ────────────────────────────────────────────────────────
    ("kylian mbappelottin",   "Spain La Liga"):            "kylian mbappe",
    ("abdelkabir abqar",      "Spain La Liga"):            "abdel abqar",
    ("abdessamad ezzalzouli", "Spain La Liga"):            "abde ezzalzouli",
    ("yuri",                  "Spain La Liga"):            "yuri berchiche",
    ("raba",                  "Spain La Liga"):            "dani raba",
    ("raul",                  "Spain La Liga"):            "raul jimenez",
    ("adria alti",            "Spain La Liga"):            "adria altimira",
    ("alfon",                 "Spain La Liga"):            "alfon gonzalez",
    ("peque",                 "Spain La Liga"):            "peque fernandez",
    ("nyom",                  "Spain La Liga"):            "allan nyom",
    ("munir",                 "Spain La Liga"):            "munir el haddadi",
    ("tasos douvikas",        "Spain La Liga"):            "anastasios douvikas",
    ("peter",                 "Spain La Liga"):            "peter federico",
    ("nianzou kouassi",       "Spain La Liga"):            "tanguy nianzou",     # Tanguy Nianzou Kouassi
    ("ezequiel avila",        "Spain La Liga"):            "chimy avila",        # Chimy = nickname for Ezequiel
}


# ══════════════════════════════════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════════════════════════════════

def _norm_name(name: str) -> str:
    """Normalise player name for cross-source matching (accents → ascii, lowercase)."""
    if not isinstance(name, str):
        return ""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    clean = re.sub(r"[^a-z0-9 ]", "", ascii_name.lower())
    return " ".join(clean.split())


def _update_freshness_record(name: str, row_count: int, path: Path) -> None:
    """Append/update fetch metadata for one raw dataset (best-effort, single-process safe)."""
    try:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        if FRESHNESS_PATH.exists():
            data = json.loads(FRESHNESS_PATH.read_text(encoding="utf-8"))
        else:
            data = {}
        data[name] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "rows": int(row_count),
            "path": str(path),
        }
        FRESHNESS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"  Could not update freshness sidecar: {e}")


def save_raw(df: pd.DataFrame, name: str) -> Path:
    """Save a DataFrame as data/raw/<name>.parquet."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path = RAW_DIR / f"{name}.parquet"
    df.to_parquet(path, index=False)
    log.info(f"  💾 {len(df)} rows → {path.name}")
    _update_freshness_record(name, len(df), path)
    return path


def fbref_season_to_understat(season: str) -> str:
    """'2025-2026' → '2025'"""
    return season.split("-")[0]


# ══════════════════════════════════════════════════════════════════════════════
#  FBref helpers
# ══════════════════════════════════════════════════════════════════════════════

def flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    new_cols, seen = [], {}
    for col in df.columns:
        if isinstance(col, tuple):
            parts = [str(p) for p in col if p and "Unnamed:" not in str(p)]
            name = parts[-1] if parts else str(col[-1])
        else:
            name = str(col)
        clean = FBREF_COL_RENAME.get(name, name)
        if clean in seen:
            if isinstance(col, tuple):
                gp = [str(p) for p in col[:-1] if p and "Unnamed:" not in str(p)]
                sfx = gp[0].lower().replace(" ","_").replace("+","_").replace("-","_") if gp else str(seen[clean])
                clean = f"{clean}_{sfx}"
            seen[clean] = seen.get(clean, 0) + 1
        else:
            seen[clean] = 0
        new_cols.append(clean)
    df = df.copy()
    df.columns = new_cols
    return df


def clean_player_df(df: pd.DataFrame, league: str, season: str, stat_category: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = flatten_columns(df)
    for col in ["player", "Player"]:
        if col in df.columns:
            df = df[df[col].notna() & ~df[col].astype(str).str.match(r"^(Player|Rk)$")]
            break
    skip = {"player","nation","pos","team","age","player_id","team_id","season","league","stat_category"}
    for col in df.columns:
        if col not in skip:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["league"]        = league
    df["season"]        = season
    df["stat_category"] = stat_category
    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════════════════════
#  FBref collection
# ══════════════════════════════════════════════════════════════════════════════

def collect_fbref(leagues=None, seasons=None, stat_categories=None, wait_time=7):
    """Scrape FBref via ScraperFC (opens real Chrome browser)."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from ScraperFC import FBref

    leagues         = leagues         or FBREF_LEAGUES
    seasons         = seasons         or SEASONS
    stat_categories = stat_categories or STAT_CATEGORIES

    fbref = FBref(wait_time=wait_time)
    total = len(leagues) * len(seasons) * len(stat_categories)
    done  = 0

    for season in seasons:
        for league in leagues:
            try:
                valid = fbref.get_valid_seasons(league)
            except Exception as e:
                log.warning(f"Could not get valid seasons for {league}: {e}")
                continue
            if season not in valid:
                log.warning(f"Season {season!r} not in FBref for {league}")
                continue

            for cat in stat_categories:
                done += 1
                fname = (f"{league.replace(' ','_')}__{season.replace('-','_')}"
                         f"__{cat.replace(' ','_')}")
                path  = RAW_DIR / f"{fname}.parquet"
                if path.exists():
                    log.info(f"[{done}/{total}] ⏭️  {path.name}")
                    continue

                log.info(f"[{done}/{total}] 📡 FBref: {league} / {season} / {cat}")
                t0 = time.time()
                try:
                    result    = fbref.scrape_stats(season, league, cat)
                    player_df = result.get("player", pd.DataFrame())
                    if player_df is not None and not player_df.empty:
                        player_df = clean_player_df(player_df, league, season, cat)
                        save_raw(player_df, fname)
                        log.info(f"  ✅ {len(player_df)} players in {time.time()-t0:.1f}s")
                    else:
                        log.warning("  ⚠️  No player data")
                except Exception as e:
                    log.error(f"  ❌ {e}")

                elapsed = time.time() - t0
                pause   = max(0, wait_time - elapsed) + random.uniform(0, 2)
                if pause > 0:
                    time.sleep(pause)


# ══════════════════════════════════════════════════════════════════════════════
#  Understat collection
# ══════════════════════════════════════════════════════════════════════════════

def collect_understat(leagues=None, seasons=None):
    """Fetch per-player xG/xA from Understat's POST API (no browser needed)."""
    leagues = leagues or list(UNDERSTAT_LEAGUES.keys())
    seasons = seasons or SEASONS
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for league in leagues:
        code = UNDERSTAT_LEAGUES.get(league)
        if not code:
            log.info(f"  Understat: skipping {league} (not Big 5)")
            continue
        for season in seasons:
            us_year  = fbref_season_to_understat(season)
            fname    = f"understat__{league.replace(' ','_')}__{season.replace('-','_')}"
            raw_path = RAW_DIR / f"{fname}.parquet"
            if raw_path.exists():
                log.info(f"⏭️  {raw_path.name}")
                continue
            log.info(f"📡 Understat: {league} {season} (year={us_year})")
            try:
                resp = requests.post(UNDERSTAT_API,
                                     data={"league": code, "season": us_year},
                                     headers=UNDERSTAT_HEADERS, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
                if not payload.get("success"):
                    log.warning(f"  Understat error: {payload}")
                    continue
                df = pd.DataFrame(payload["players"]).rename(columns={
                    "player_name": "player", "time": "us_minutes", "games": "us_games",
                    "goals": "us_goals", "xG": "xg", "assists": "us_assists", "xA": "xag",
                    "shots": "us_shots", "key_passes": "us_key_passes",
                    "yellow_cards": "us_yellow_cards", "red_cards": "us_red_cards",
                    "position": "us_pos", "team_title": "us_team",
                    "npg": "us_npg", "npxG": "npxg",
                    "xGChain": "xg_chain", "xGBuildup": "xg_buildup",
                })
                num = ["us_minutes","us_games","us_goals","xg","us_assists","xag",
                       "us_shots","us_key_passes","us_yellow_cards","us_red_cards",
                       "us_npg","npxg","xg_chain","xg_buildup"]
                for col in num:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df["league"]     = league
                df["season"]     = season
                df["_name_norm"] = df["player"].apply(_norm_name)
                save_raw(df, fname)
                log.info(f"  ✅ {len(df)} players")
            except Exception as e:
                log.error(f"  ❌ Understat {league} {season}: {e}")
            time.sleep(2)


# ══════════════════════════════════════════════════════════════════════════════
#  SofaScore collection
# ══════════════════════════════════════════════════════════════════════════════

def collect_sofascore(leagues=None, seasons=None):
    """
    Collect all player stats from SofaScore via ScraperFC.
    Runs headless Chrome (botasaurus) — no visible browser window.
    Covers 37 leagues/competitions including UCL, Europa League, Eredivisie, etc.
    """
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from ScraperFC import Sofascore
    from ScraperFC.utils import get_module_comps

    all_ss_leagues = list(get_module_comps("SOFASCORE").keys())
    leagues = leagues or SOFASCORE_TARGET_LEAGUES
    seasons = seasons or SEASONS

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ss = Sofascore()

    total = len(leagues) * len(seasons)
    done  = 0

    for league in leagues:
        if league not in all_ss_leagues:
            log.info(f"  SofaScore: {league!r} not supported, skipping")
            continue

        try:
            with _silence_bota_noise():
                valid = _ss_retry(
                    ss.get_valid_seasons,
                    league,
                    label=f"get_valid_seasons({league})",
                )
        except Exception as e:
            log.warning(f"  SofaScore get_valid_seasons failed for {league}: {e}")
            continue

        for season in seasons:
            done += 1
            ss_year = SOFASCORE_SEASONS.get(season)
            if not ss_year or ss_year not in valid:
                log.info(f"[{done}/{total}] ⏭️  {league} {season} — not in SofaScore ({ss_year!r})")
                continue

            fname    = f"sofascore__{league.replace(' ','_')}__{season.replace('-','_')}"
            raw_path = RAW_DIR / f"{fname}.parquet"
            if raw_path.exists():
                log.info(f"[{done}/{total}] ⏭️  {raw_path.name}")
                continue

            log.info(f"[{done}/{total}] 📡 SofaScore: {league} {season} ({ss_year})")
            t0 = time.time()
            try:
                with _silence_bota_noise():
                    df = ss.scrape_player_league_stats(ss_year, league, accumulation="total")

                if df is None or df.empty:
                    log.warning(f"  ⚠️  Empty result for {league} {season}")
                    continue

                # Rename columns
                df = df.rename(columns=SS_COL_RENAME)
                # Drop any duplicate columns that arise from rename collisions
                df = df.loc[:, ~df.columns.duplicated(keep="first")]

                # Numeric conversion for all stat columns
                skip = {"player", "team", "league", "season", "_name_norm",
                        "sofascore_id", "sofascore_team_id"}
                for col in df.columns:
                    if col not in skip:
                        df[col] = pd.to_numeric(df[col], errors="coerce")

                df["league"]     = league
                df["season"]     = season
                df["_name_norm"] = df["player"].apply(_norm_name)

                save_raw(df, fname)
                log.info(f"  ✅ {len(df)} players in {time.time()-t0:.1f}s")

            except Exception as e:
                log.error(f"  ❌ SofaScore {league} {season}: {e}")

            time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  SofaScore retry helper — mimics collect_sofascore's implicit "re-run to retry"
# ══════════════════════════════════════════════════════════════════════════════

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
            # Treat empty-ish returns as transient failures worth retrying
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
    # All attempts returned empty — return the last (empty) result
    return fn(*args, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
#  ClubElo — global ratings + upcoming fixtures (plain HTTP, no browser)
# ══════════════════════════════════════════════════════════════════════════════

CLUBELO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}


def _clubelo_scoreline_probs(row: pd.Series) -> tuple[float, float, float]:
    """Sum R:a-b columns into home win / draw / away win probabilities."""
    hw, dr, aw = 0.0, 0.0, 0.0
    for col, val in row.items():
        if not isinstance(col, str) or not col.startswith("R:"):
            continue
        mid = col[2:]
        if "-" not in mid:
            continue
        try:
            a_str, b_str = mid.split("-", 1)
            ha, aww = int(a_str), int(b_str)
            p = float(val)
        except (ValueError, TypeError):
            continue
        if ha > aww:
            hw += p
        elif ha == aww:
            dr += p
        else:
            aw += p
    return hw, dr, aw


def collect_clubelo(date: str | None = None) -> None:
    """
    Fetch ClubElo global snapshot for one calendar day + upcoming fixtures CSV.
    Uses http://api.clubelo.com (HTTPS often times out from some networks).
    """
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from ScraperFC import ClubElo

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    day = date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        log.error(f"  ClubElo: invalid date {day!r}, expected YYYY-MM-DD")
        return

    slug_date = day.replace("-", "_")
    global_name = f"clubelo__global__{slug_date}"
    fix_name = f"clubelo__fixtures__{slug_date}"
    global_path = RAW_DIR / f"{global_name}.parquet"
    fix_path = RAW_DIR / f"{fix_name}.parquet"

    ce = ClubElo()

    if not global_path.exists():
        try:
            log.info(f"📡 ClubElo global snapshot: {day}")
            gdf = ce.scrape_date(day)
            gdf = gdf.rename(
                columns={
                    "Rank": "rank",
                    "Club": "club",
                    "Country": "country",
                    "Level": "level",
                    "Elo": "elo",
                    "From": "valid_from",
                    "To": "valid_to",
                }
            )
            gdf["snapshot_date"] = day
            save_raw(gdf, global_name)
        except Exception as e:
            log.error(f"  ❌ ClubElo global {day}: {e}")
    else:
        log.info(f"⏭️  {global_name}.parquet already exists")

    if not fix_path.exists():
        try:
            log.info("📡 ClubElo fixtures (upcoming)")
            url = "http://api.clubelo.com/Fixtures"
            r = requests.get(url, headers=CLUBELO_HEADERS, timeout=30)
            r.raise_for_status()
            fdf = pd.read_csv(StringIO(r.text))
            probs = fdf.apply(_clubelo_scoreline_probs, axis=1, result_type="expand")
            fdf = fdf.rename(
                columns={
                    "Date": "date",
                    "Country": "country",
                    "Home": "home_team",
                    "Away": "away_team",
                }
            )
            fdf["home_win_prob"] = probs[0]
            fdf["draw_prob"] = probs[1]
            fdf["away_win_prob"] = probs[2]
            fdf["snapshot_date"] = day
            keep = [
                c
                for c in fdf.columns
                if c
                in {
                    "date",
                    "country",
                    "home_team",
                    "away_team",
                    "home_win_prob",
                    "draw_prob",
                    "away_win_prob",
                    "snapshot_date",
                }
            ]
            save_raw(fdf[keep], fix_name)
            log.info(f"  ✅ ClubElo fixtures: {len(fdf)} rows")
        except Exception as e:
            log.error(f"  ❌ ClubElo fixtures: {e}")
    else:
        log.info(f"⏭️  {fix_name}.parquet already exists")


# ══════════════════════════════════════════════════════════════════════════════
#  SofaScore — per-match shots, team stats, player stats, momentum
# ══════════════════════════════════════════════════════════════════════════════

# SofaScore team-match-stat keys we persist (match-level; not duplicated in season parquet).
_SS_TEAM_STAT_KEYS = {
    "ballPossession": ("ball_possession", "pct"),
    "expectedGoals": ("expected_goals", "float"),
    "shotsOnGoal": ("shots_on_target", "float"),
    "shotsOffGoal": ("shots_off_target", "float"),
    "totalShotsOnGoal": ("total_shots", "float"),
    "totalShotsInsideBox": ("shots_inside_box", "float"),
    "totalShotsOutsideBox": ("shots_outside_box", "float"),
    "finalThirdEntries": ("final_third_entries", "float"),
    "touchesInOppBox": ("touches_in_opp_box", "float"),
    "offsides": ("offsides", "float"),
    "cornerKicks": ("corner_kicks", "float"),
    "bigChanceCreated": ("big_chances", "float"),
}


def _sofascore_team_stats_to_rows(
    team_df: pd.DataFrame,
    match_id: int,
    home_team: str,
    away_team: str,
    league: str,
    season: str,
) -> list[dict]:
    """Pivot long SofaScore team match stats → one dict per period (ALL / 1ST / 2ND)."""
    if team_df.empty or "period" not in team_df.columns or "key" not in team_df.columns:
        return []
    rows: list[dict] = []
    for period in sorted(team_df["period"].unique()):
        sub = team_df[team_df["period"] == period]
        row: dict = {
            "match_id": match_id,
            "home_team": home_team,
            "away_team": away_team,
            "league": league,
            "season": season,
            "period": str(period),
        }
        for api_key, (prefix, _kind) in _SS_TEAM_STAT_KEYS.items():
            hit = sub[sub["key"] == api_key]
            if hit.empty:
                row[f"{prefix}_home"] = None
                row[f"{prefix}_away"] = None
                continue
            r0 = hit.iloc[0]
            hv = r0.get("homeValue")
            av = r0.get("awayValue")
            # Prefer numeric homeValue/awayValue when present (SofaScore API).
            row[f"{prefix}_home"] = float(hv) if pd.notna(hv) else None
            row[f"{prefix}_away"] = float(av) if pd.notna(av) else None
        rows.append(row)
    return rows


def _sofascore_shots_slim(shots: pd.DataFrame, match_id: int, league: str, season: str) -> pd.DataFrame:
    """Normalize shotmap API frame to our slim schema."""
    if shots.empty:
        return pd.DataFrame()
    out = shots.copy()
    # Normalise xG column name from API
    for cand in ("xg", "xG", "expectedGoals"):
        if cand in out.columns and "xg" not in out.columns:
            out = out.rename(columns={cand: "xg"})
            break
    for cand in ("xgot", "xGoT", "expectedGoalsOnTarget"):
        if cand in out.columns and "xgot" not in out.columns:
            out = out.rename(columns={cand: "xgot"})
            break
    if "player" in out.columns:
        out["player_id"] = out["player"].apply(
            lambda x: x.get("id") if isinstance(x, dict) else None
        )
        out["player_name"] = out["player"].apply(
            lambda x: x.get("name") if isinstance(x, dict) else str(x)
        )
        out = out.drop(columns=["player"], errors="ignore")
    coords = out.get("playerCoordinates")
    if coords is not None:
        out["player_x"] = coords.apply(lambda x: x.get("x") if isinstance(x, dict) else None)
        out["player_y"] = coords.apply(lambda x: x.get("y") if isinstance(x, dict) else None)
        out["player_z"] = coords.apply(lambda x: x.get("z") if isinstance(x, dict) else None)
        out = out.drop(columns=["playerCoordinates"], errors="ignore")
    gm = out.get("goalMouthCoordinates")
    if gm is not None:
        out["goal_mouth_x"] = gm.apply(lambda x: x.get("x") if isinstance(x, dict) else None)
        out["goal_mouth_y"] = gm.apply(lambda x: x.get("y") if isinstance(x, dict) else None)
        out["goal_mouth_z"] = gm.apply(lambda x: x.get("z") if isinstance(x, dict) else None)
        out = out.drop(columns=["goalMouthCoordinates"], errors="ignore")
    out["match_id"] = match_id
    out["league"] = league
    out["season"] = season
    want = [
        "match_id",
        "player_id",
        "player_name",
        "isHome",
        "time",
        "shotType",
        "situation",
        "bodyPart",
        "xg",
        "xgot",
        "goalMouthLocation",
        "player_x",
        "player_y",
        "goal_mouth_x",
        "goal_mouth_y",
        "league",
        "season",
    ]
    for c in want:
        if c not in out.columns:
            out[c] = None
    slim = out[want].rename(
        columns={
            "isHome": "is_home",
            "time": "minute",
            "shotType": "shot_type",
            "bodyPart": "body_part",
            "goalMouthLocation": "goal_mouth_location",
        }
    )
    return slim


def _sofascore_player_match_slim(
    pm: pd.DataFrame, match_id: int, home_id: int, league: str, season: str
) -> pd.DataFrame:
    """Keep only columns not already covered by season-level SofaScore parquet."""
    if pm.empty:
        return pd.DataFrame()
    df = pm.loc[:, ~pm.columns.duplicated()].copy()
    df["match_id"] = match_id
    df["league"] = league
    df["season"] = season
    df["is_home"] = df["teamId"].apply(lambda tid: bool(tid == home_id) if pd.notna(tid) else None)
    rename = {
        "id": "player_id",
        "name": "player_name",
        "teamId": "team_id",
        "teamName": "team_name",
        "substitute": "substitute",
        "minutesPlayed": "minutes_played",
        "rating": "rating",
        "expectedGoals": "xg",
        "expectedGoalsOnTarget": "xgot",
        "expectedAssists": "xa",
        "goals": "goals",
        "goalAssist": "assists",
        "totalShots": "total_shots",
        "onTargetScoringAttempt": "shots_on_target",
        "totalPass": "total_passes",
        "accuratePass": "accurate_passes",
        "touches": "touches",
        "possessionLostCtrl": "possession_lost",
        "duelWon": "duels_won",
        "duelLost": "duels_lost",
        "fouls": "fouls",
        "wasFouled": "was_fouled",
    }
    for old, new in rename.items():
        if old in df.columns and new not in df.columns:
            df[new] = df[old]
    cols = [
        "match_id",
        "player_id",
        "player_name",
        "team_id",
        "team_name",
        "is_home",
        "substitute",
        "minutes_played",
        "rating",
        "xg",
        "xgot",
        "xa",
        "goals",
        "assists",
        "total_shots",
        "shots_on_target",
        "total_passes",
        "accurate_passes",
        "touches",
        "possession_lost",
        "duels_won",
        "duels_lost",
        "fouls",
        "was_fouled",
        "league",
        "season",
    ]
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]


def _ckpt_flush(
    slug: str,
    done_ids: set,
    all_shots: list,
    all_team_rows: list,
    all_play: list,
    all_mom: list,
    p_shots: Path,
    p_team: Path,
    p_play: Path,
    p_mom: Path,
    p_ckpt: Path,
) -> None:
    """
    Flush accumulated in-memory data to partial parquet files and write the
    checkpoint JSON.  Called every FLUSH_EVERY matches so a crashed run can
    resume from the last flush point rather than starting over.
    """
    try:
        if all_shots:
            pd.concat(all_shots, ignore_index=True).to_parquet(p_shots, index=False)
        if all_team_rows:
            pd.DataFrame(all_team_rows).to_parquet(p_team, index=False)
        if all_play:
            pd.concat(all_play, ignore_index=True).to_parquet(p_play, index=False)
        if all_mom:
            pd.concat(all_mom, ignore_index=True).to_parquet(p_mom, index=False)
        p_ckpt.write_text(
            json.dumps({"slug": slug, "done_ids": sorted(done_ids)}),
            encoding="utf-8",
        )
    except Exception as exc:
        log.warning(f"  ⚠️  Checkpoint flush failed: {exc}")


def _discover_sofascore_match_jobs(
    leagues: list[str] | None,
    seasons: list[str] | None,
    sleep_between_matches: float,
    force: bool,
) -> list[tuple[str, str, float, bool]]:
    """
    Build the list of (league, season, sleep_between_matches, force) tuples that
    still need scraping.  Skips unsupported leagues, unavailable seasons, and
    league-seasons whose four parquet outputs already exist (unless force=True).
    """
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from ScraperFC import Sofascore
    from ScraperFC.utils import get_module_comps

    all_ss_leagues = list(get_module_comps("SOFASCORE").keys())
    use_leagues = leagues or list(SOFASCORE_TARGET_LEAGUES)
    use_seasons = seasons or list(SEASONS)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ss = Sofascore()
    jobs: list[tuple[str, str, float, bool]] = []

    for league in use_leagues:
        if league not in all_ss_leagues:
            log.info(f"  SofaScore matches: {league!r} not supported, skipping")
            continue
        try:
            with _silence_bota_noise():
                valid = _ss_retry(
                    ss.get_valid_seasons,
                    league,
                    label=f"get_valid_seasons({league})",
                )
        except Exception as e:
            log.warning(f"  SofaScore matches get_valid_seasons failed for {league}: {e}")
            continue

        for season in use_seasons:
            ss_year = SOFASCORE_SEASONS.get(season)
            if not ss_year or ss_year not in valid:
                log.info(
                    f"  SofaScore matches: {league} {season} — not available ({ss_year!r})"
                )
                continue

            slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
            p_shots = RAW_DIR / f"sofascore_match_shots__{slug}.parquet"
            p_team = RAW_DIR / f"sofascore_match_team_stats__{slug}.parquet"
            p_play = RAW_DIR / f"sofascore_match_player_stats__{slug}.parquet"
            p_mom = RAW_DIR / f"sofascore_match_momentum__{slug}.parquet"
            if not force and p_shots.exists() and p_team.exists() and p_play.exists() and p_mom.exists():
                log.info(f"⏭️  SofaScore match pack complete for {slug}")
                continue
            jobs.append((league, season, sleep_between_matches, force))
    return jobs


def _sofascore_match_season_worker(
    job: tuple[str, str, float, bool],
) -> None:
    """
    Scrape one league+season match pack (four parquets).  Top-level so it can be
    pickled for ProcessPoolExecutor (macOS spawn).
    """
    league, season, sleep_between_matches, force = job
    pfx = f"[{league} | {season}]"

    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from ScraperFC import Sofascore
    from ScraperFC.utils import get_module_comps

    all_ss_leagues = list(get_module_comps("SOFASCORE").keys())
    if league not in all_ss_leagues:
        log.info(f"{pfx}  not supported, skipping")
        return

    ss = Sofascore()
    try:
        with _silence_bota_noise():
            valid = _ss_retry(
                ss.get_valid_seasons,
                league,
                label=f"get_valid_seasons({league})",
            )
    except Exception as e:
        log.warning(f"{pfx}  get_valid_seasons failed: {e}")
        return

    ss_year = SOFASCORE_SEASONS.get(season)
    if not ss_year or ss_year not in valid:
        log.info(f"{pfx}  season not on SofaScore ({ss_year!r}), skipping")
        return

    slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
    p_shots = RAW_DIR / f"sofascore_match_shots__{slug}.parquet"
    p_team = RAW_DIR / f"sofascore_match_team_stats__{slug}.parquet"
    p_play = RAW_DIR / f"sofascore_match_player_stats__{slug}.parquet"
    p_mom = RAW_DIR / f"sofascore_match_momentum__{slug}.parquet"
    p_ckpt = RAW_DIR / f"sofascore_match_checkpoint__{slug}.json"

    if not force and p_shots.exists() and p_team.exists() and p_play.exists() and p_mom.exists():
        log.info(f"{pfx}  ⏭️  match pack already complete")
        return

    done_ids: set[int] = set()
    if not force and p_ckpt.exists():
        try:
            done_ids = set(json.loads(p_ckpt.read_text()).get("done_ids", []))
        except Exception:
            pass

    def _load_partial(path: Path) -> pd.DataFrame:
        if path.exists() and done_ids:
            try:
                return pd.read_parquet(path)
            except Exception:
                pass
        return pd.DataFrame()

    all_shots: list[pd.DataFrame] = [df for df in [_load_partial(p_shots)] if not df.empty]
    all_team_rows: list[dict] = (
        _load_partial(p_team).to_dict("records") if p_team.exists() and done_ids else []
    )
    all_play: list[pd.DataFrame] = [df for df in [_load_partial(p_play)] if not df.empty]
    all_mom: list[pd.DataFrame] = [df for df in [_load_partial(p_mom)] if not df.empty]

    if done_ids:
        log.info(f"{pfx}  📂 resuming: {len(done_ids)} matches already done")

    log.info(f"{pfx}  📡 SofaScore matches ({ss_year})")
    log.info(f"{pfx}  ⏳ fetching match list …")
    try:
        with _silence_bota_noise():
            match_dicts = _ss_retry(
                ss.get_match_dicts,
                ss_year,
                league,
                label=f"get_match_dicts({league} {ss_year})",
            )
    except Exception as e:
        log.error(f"{pfx}  ❌ get_match_dicts: {e}")
        return

    finished = [
        m
        for m in match_dicts
        if isinstance(m.get("status"), dict) and m["status"].get("type") == "finished"
    ]
    match_seq = {int(m["id"]): i for i, m in enumerate(finished, 1)}
    remaining = [m for m in finished if int(m["id"]) not in done_ids]
    log.info(
        f"{pfx}  ✅ {len(finished)} finished matches total, "
        f"{len(remaining)} remaining to scrape"
    )

    if not remaining:
        log.info(f"{pfx}  all matches already done — writing final parquets")
    else:
        FLUSH_EVERY = 25

        for idx, m in enumerate(remaining, 1):
            mid = int(m["id"])
            home = m.get("homeTeam", {}) or {}
            away = m.get("awayTeam", {}) or {}
            home_team = home.get("name", "")
            away_team = away.get("name", "")
            home_id = int(home.get("id", 0))

            global_idx = match_seq.get(mid, idx)
            log.info(
                f"{pfx}  [{global_idx}/{len(finished)}] "
                f"{home_team} vs {away_team} (id={mid})"
            )

            try:
                with _silence_bota_noise():
                    sdf = _ss_retry(ss.scrape_match_shots, mid, label=f"shots {mid}")
                all_shots.append(_sofascore_shots_slim(sdf, mid, league, season))
            except Exception as e:
                log.warning(f"{pfx}    shots match {mid}: {e}")

            try:
                with _silence_bota_noise():
                    tdf = _ss_retry(
                        ss.scrape_team_match_stats, mid, label=f"team_stats {mid}"
                    )
                all_team_rows.extend(
                    _sofascore_team_stats_to_rows(
                        tdf, mid, home_team, away_team, league, season
                    )
                )
            except Exception as e:
                log.warning(f"{pfx}    team stats match {mid}: {e}")

            try:
                with _silence_bota_noise():
                    pdf = _ss_retry(
                        ss.scrape_player_match_stats, mid, label=f"player_stats {mid}"
                    )
                all_play.append(
                    _sofascore_player_match_slim(pdf, mid, home_id, league, season)
                )
            except Exception as e:
                log.warning(f"{pfx}    player stats match {mid}: {e}")

            try:
                with _silence_bota_noise():
                    mdf = _ss_retry(ss.scrape_match_momentum, mid, label=f"momentum {mid}")
                if not mdf.empty:
                    mdf = mdf.copy()
                    mdf["match_id"] = mid
                    mdf["league"] = league
                    mdf["season"] = season
                    all_mom.append(mdf)
            except Exception as e:
                log.warning(f"{pfx}    momentum match {mid}: {e}")

            done_ids.add(mid)
            time.sleep(sleep_between_matches)

            if idx % FLUSH_EVERY == 0:
                log.info(f"{pfx}  💾 checkpoint flush at {global_idx}/{len(finished)} …")
                _ckpt_flush(
                    slug,
                    done_ids,
                    all_shots,
                    all_team_rows,
                    all_play,
                    all_mom,
                    p_shots,
                    p_team,
                    p_play,
                    p_mom,
                    p_ckpt,
                )

    shots_df = pd.concat(all_shots, ignore_index=True) if all_shots else pd.DataFrame()
    team_df = pd.DataFrame(all_team_rows) if all_team_rows else pd.DataFrame()
    play_df = pd.concat(all_play, ignore_index=True) if all_play else pd.DataFrame()
    mom_df = pd.concat(all_mom, ignore_index=True) if all_mom else pd.DataFrame()

    save_raw(shots_df, f"sofascore_match_shots__{slug}")
    save_raw(team_df, f"sofascore_match_team_stats__{slug}")
    save_raw(play_df, f"sofascore_match_player_stats__{slug}")
    save_raw(mom_df, f"sofascore_match_momentum__{slug}")

    if p_ckpt.exists():
        p_ckpt.unlink()

    log.info(
        f"{pfx}  ✅ saved: shots={len(shots_df)} team_rows={len(team_df)} "
        f"player_rows={len(play_df)} momentum_rows={len(mom_df)}"
    )


def collect_sofascore_matches(
    leagues=None,
    seasons=None,
    sleep_between_matches: float = 2.0,
    force: bool = False,
    parallel: int = 1,
) -> None:
    """
    Per-match SofaScore data: shot map, team stats (by half), player match stats, momentum.
    Writes four parquet files per league+season. Skips a league+season if all four exist
    unless force=True.

    parallel: if >1, run that many league-season jobs at once in separate processes
    (safe with Bright Data — each job writes different files; IPs rotate per request).
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    jobs = _discover_sofascore_match_jobs(leagues, seasons, sleep_between_matches, force)
    if not jobs:
        log.info("SofaScore matches: nothing to do (all packs complete or unavailable).")
        return

    log.info(f"SofaScore match jobs queued: {len(jobs)}  (parallel={parallel})")

    if parallel <= 1:
        for job in jobs:
            _sofascore_match_season_worker(job)
        return

    with concurrent.futures.ProcessPoolExecutor(max_workers=parallel) as pool:
        futures = {
            pool.submit(_sofascore_match_season_worker, job): job for job in jobs
        }
        for fut in concurrent.futures.as_completed(futures):
            job = futures[fut]
            try:
                fut.result()
            except Exception as exc:
                log.error(
                    f"SofaScore match job crashed: {job[0]} {job[1]} — {exc}"
                )


# ══════════════════════════════════════════════════════════════════════════════
#  Understat AJAX helpers
# ══════════════════════════════════════════════════════════════════════════════

def _us_ajax_get(path: str, referer: str | None = None) -> dict:
    """GET request to a Understat AJAX endpoint with retries."""
    headers = {**UNDERSTAT_AJAX_HEADERS, "Referer": referer or UNDERSTAT_AJAX_BASE}
    for attempt in range(3):
        try:
            r = requests.get(UNDERSTAT_AJAX_BASE + path, headers=headers, timeout=20)
            if r.status_code == 200 and len(r.content) > 10:
                return r.json()
        except Exception:
            pass
        time.sleep(attempt + 2)
    return {}


# ══════════════════════════════════════════════════════════════════════════════
#  Understat — league tables
# ══════════════════════════════════════════════════════════════════════════════

def collect_understat_league_tables(leagues=None, seasons=None):
    """Scrape overall/home/away league tables from Understat.  Big 5 only."""
    leagues = leagues or list(UNDERSTAT_LEAGUES.keys())
    seasons = seasons or SEASONS
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for league in leagues:
        code = UNDERSTAT_LEAGUES.get(league)
        if not code:
            continue
        for season in seasons:
            us_year = fbref_season_to_understat(season)
            slug    = f"{league.replace(' ','_')}__{season.replace('-','_')}"
            overall_path = RAW_DIR / f"understat_league_table__{slug}__overall.parquet"
            if overall_path.exists():
                log.info(f"⏭️  understat_league_table__{slug}__overall.parquet")
                continue

            log.info(f"📡 Understat league table: {league} {season}")
            data = _us_ajax_get(f"getLeagueData/{code}/{us_year}",
                                referer=f"https://understat.com/league/{code}/{us_year}")
            if not data:
                log.warning(f"  ⚠️  No data for {league} {season}")
                continue

            teams = data.get("teams", {})
            rows  = []
            for t in teams.values():
                for match in t.get("history", []):
                    rows.append({
                        "team_id":           t["id"],
                        "team":              t["title"],
                        "h_a":               match.get("h_a"),
                        "xG":                float(match.get("xG", 0)),
                        "xGA":               float(match.get("xGA", 0)),
                        "npxG":              float(match.get("npxG", 0)),
                        "npxGA":             float(match.get("npxGA", 0)),
                        "ppda_att":          match.get("ppda", {}).get("att", 0),
                        "ppda_def":          match.get("ppda", {}).get("def", 1),
                        "ppda_allowed_att":  match.get("ppda_allowed", {}).get("att", 0),
                        "ppda_allowed_def":  match.get("ppda_allowed", {}).get("def", 1),
                        "deep":              int(match.get("deep", 0)),
                        "deep_allowed":      int(match.get("deep_allowed", 0)),
                        "goals":             int(match.get("scored", 0)),
                        "goals_against":     int(match.get("missed", 0)),
                        "xpts":              float(match.get("xpts", 0)),
                        "wins":              int(match.get("wins", 0)),
                        "draws":             int(match.get("draws", 0)),
                        "losses":            int(match.get("loses", 0)),
                        "pts":               int(match.get("pts", 0)),
                        "npxGD":             float(match.get("npxGD", 0)),
                        "date":              match.get("date"),
                    })
            if not rows:
                continue

            df = pd.DataFrame(rows)
            agg = {
                "wins": "sum", "draws": "sum", "losses": "sum", "pts": "sum",
                "goals": "sum", "goals_against": "sum",
                "xG": "sum", "xGA": "sum", "npxG": "sum", "npxGA": "sum", "npxGD": "sum",
                "xpts": "sum", "deep": "sum", "deep_allowed": "sum",
                "ppda_att": "sum", "ppda_def": "sum",
                "ppda_allowed_att": "sum", "ppda_allowed_def": "sum",
            }

            def _build_table(sub):
                t = sub.groupby(["team_id", "team"], as_index=False).agg(agg)
                t["M"]     = t["wins"] + t["draws"] + t["losses"]
                t["PPDA"]  = (t["ppda_att"] / t["ppda_def"].replace(0, 1)).round(2)
                t["OPPDA"] = (t["ppda_allowed_att"] / t["ppda_allowed_def"].replace(0, 1)).round(2)
                t = t.drop(columns=["ppda_att", "ppda_def", "ppda_allowed_att", "ppda_allowed_def"])
                t["league"] = league
                t["season"] = season
                return t.sort_values("pts", ascending=False).reset_index(drop=True)

            save_raw(_build_table(df),                      f"understat_league_table__{slug}__overall")
            save_raw(_build_table(df[df["h_a"] == "h"]),   f"understat_league_table__{slug}__home")
            save_raw(_build_table(df[df["h_a"] == "a"]),   f"understat_league_table__{slug}__away")
            log.info(f"  ✅ {len(teams)} teams")
            time.sleep(1)


# ══════════════════════════════════════════════════════════════════════════════
#  Understat — match shots + rosters
# ══════════════════════════════════════════════════════════════════════════════

def collect_understat_matches(leagues=None, seasons=None, sleep=0.5):
    """Collect shot-level data and player-per-match rosters from Understat.  Big 5 only."""
    leagues = leagues or list(UNDERSTAT_LEAGUES.keys())
    seasons = seasons or SEASONS
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for league in leagues:
        code = UNDERSTAT_LEAGUES.get(league)
        if not code:
            continue
        for season in seasons:
            us_year = fbref_season_to_understat(season)
            slug    = f"{league.replace(' ','_')}__{season.replace('-','_')}"
            shots_path = RAW_DIR / f"understat_match_shots__{slug}.parquet"
            if shots_path.exists():
                log.info(f"⏭️  understat_match_shots__{slug}.parquet")
                continue

            # Step 1: get match list for this season
            log.info(f"📡 Understat matches: {league} {season} — fetching match IDs")
            league_data = _us_ajax_get(f"getLeagueData/{code}/{us_year}",
                                       referer=f"https://understat.com/league/{code}/{us_year}")
            if not league_data:
                log.warning(f"  ⚠️  No league data for {league} {season}")
                continue

            completed = [m for m in league_data.get("dates", []) if m.get("isResult")]
            log.info(f"  {len(completed)} completed matches to fetch")

            # Save match info from league data (fast, no extra requests)
            info_path = RAW_DIR / f"understat_match_info__{slug}.parquet"
            if not info_path.exists():
                info_rows = []
                for m in completed:
                    info_rows.append({
                        "match_id":      int(m["id"]),
                        "home_team":     m["h"]["title"],
                        "away_team":     m["a"]["title"],
                        "home_team_id":  m["h"]["id"],
                        "away_team_id":  m["a"]["id"],
                        "home_goals":    int(m["goals"]["h"]),
                        "away_goals":    int(m["goals"]["a"]),
                        "home_xg":       float(m["xG"]["h"]),
                        "away_xg":       float(m["xG"]["a"]),
                        "datetime":      m.get("datetime"),
                        "league":        league,
                        "season":        season,
                    })
                save_raw(pd.DataFrame(info_rows), f"understat_match_info__{slug}")

            # Step 2: fetch each match for shots + rosters
            all_shots, all_rosters = [], []
            for i, match in enumerate(completed, 1):
                mid = match["id"]
                if i % 50 == 0:
                    log.info(f"  [{i}/{len(completed)}] fetching match {mid}")

                mdata = _us_ajax_get(f"getMatchData/{mid}",
                                     referer=f"https://understat.com/match/{mid}")
                if not mdata:
                    time.sleep(sleep)
                    continue

                # Shots
                shots_raw = mdata.get("shots", {})
                for h_a, shot_list in shots_raw.items():
                    for s in (shot_list if isinstance(shot_list, list) else []):
                        all_shots.append({
                            "match_id":        int(mid),
                            "player_id":       s.get("player_id"),
                            "player":          s.get("player"),
                            "h_a":             h_a,
                            "minute":          s.get("minute"),
                            "result":          s.get("result"),
                            "X":               float(s.get("X", 0)),
                            "Y":               float(s.get("Y", 0)),
                            "xG":              float(s.get("xG", 0)),
                            "situation":       s.get("situation"),
                            "shot_type":       s.get("shotType"),
                            "player_assisted": s.get("player_assisted"),
                            "last_action":     s.get("lastAction"),
                            "league":          league,
                            "season":          season,
                        })

                # Rosters
                for h_a, players in mdata.get("rosters", {}).items():
                    for entry in (players.values() if isinstance(players, dict) else []):
                        all_rosters.append({
                            "match_id":        int(mid),
                            "roster_entry_id": entry.get("id"),
                            "player_id":       entry.get("player_id"),
                            "player":          entry.get("player"),
                            "team_id":         entry.get("team_id"),
                            "position":        entry.get("position"),
                            "h_a":             h_a,
                            "minutes":         int(entry.get("time", 0)),
                            "goals":           int(entry.get("goals", 0)),
                            "own_goals":       int(entry.get("own_goals", 0)),
                            "shots":           int(entry.get("shots", 0)),
                            "xG":              float(entry.get("xG", 0)),
                            "assists":         int(entry.get("assists", 0)),
                            "xA":              float(entry.get("xA", 0)),
                            "xGChain":         float(entry.get("xGChain", 0)),
                            "xGBuildup":       float(entry.get("xGBuildup", 0)),
                            "yellow_card":     int(entry.get("yellow_card", 0)),
                            "red_card":        int(entry.get("red_card", 0)),
                            "key_passes":      int(entry.get("key_passes", 0)),
                            "roster_in":       entry.get("roster_in"),
                            "roster_out":      entry.get("roster_out"),
                            "position_order":  entry.get("positionOrder"),
                            "league":          league,
                            "season":          season,
                        })

                time.sleep(sleep)

            if all_shots:
                save_raw(pd.DataFrame(all_shots),   f"understat_match_shots__{slug}")
            if all_rosters:
                save_raw(pd.DataFrame(all_rosters), f"understat_rosters__{slug}")
            log.info(f"  ✅ {len(all_shots)} shots, {len(all_rosters)} roster entries")


# ══════════════════════════════════════════════════════════════════════════════
#  Transfermarkt helpers + collection
# ══════════════════════════════════════════════════════════════════════════════

def _parse_tm_value(s: str) -> float | None:
    """Parse '€45.00m' or '€500k' → float in EUR."""
    if not isinstance(s, str):
        return None
    s = s.replace("€", "").replace(",", "").strip()
    try:
        if "m" in s.lower():
            return float(s.lower().replace("m", "").strip()) * 1_000_000
        if "k" in s.lower():
            return float(s.lower().replace("k", "").strip()) * 1_000
        return float(s)
    except (ValueError, AttributeError):
        return None


def _tm_season(season: str, valid_seasons: dict) -> str | None:
    """Map 'YYYY-YYYY' to whatever key Transfermarkt uses (e.g. '24/25')."""
    start, end = season.split("-")
    s2, e2 = start[2:], end[2:]
    for fmt in [f"{s2}/{e2}", f"{start}/{e2}", f"{start}/{end}"]:
        if fmt in valid_seasons:
            return fmt
    for k in valid_seasons:
        if start in k or s2 in k:
            return k
    return None


def collect_transfermarkt(leagues=None, seasons=None, sleep=2.0):
    """Scrape player profiles from Transfermarkt for all supported leagues."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    from ScraperFC import Transfermarkt
    from ScraperFC.utils import get_module_comps

    all_tm = list(get_module_comps("TRANSFERMARKT").keys())
    leagues = leagues or all_tm
    seasons = seasons or SEASONS
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    tm = Transfermarkt()
    for league in leagues:
        if league not in all_tm:
            log.info(f"  TM: {league!r} not supported, skipping")
            continue

        try:
            valid = tm.get_valid_seasons(league)
        except Exception as e:
            log.warning(f"  TM get_valid_seasons failed for {league}: {e}")
            continue

        for season in seasons:
            tm_year = _tm_season(season, valid)
            if not tm_year:
                log.info(f"  TM: {league} {season} not available (got {list(valid.keys())[:3]})")
                continue

            slug      = f"{league.replace(' ','_')}__{season.replace('-','_')}"
            flat_path = RAW_DIR / f"transfermarkt__{slug}.parquet"
            if flat_path.exists():
                log.info(f"⏭️  transfermarkt__{slug}.parquet")
                continue

            log.info(f"📡 Transfermarkt: {league} {season} ({tm_year})")
            try:
                player_links = tm.get_player_links(tm_year, league)
            except Exception as e:
                log.error(f"  ❌ get_player_links: {e}")
                continue

            log.info(f"  {len(player_links)} players to scrape")
            flat_rows, mv_rows, transfer_rows = [], [], []

            for j, link in enumerate(player_links):
                try:
                    pdf = tm.scrape_player(link)
                    if pdf.empty:
                        continue
                    row   = pdf.iloc[0]
                    tm_id = str(row.get("ID", ""))

                    flat_rows.append({
                        "tm_id":               tm_id,
                        "tm_name":             row.get("Name"),
                        "market_value_str":    row.get("Value"),
                        "market_value_eur":    _parse_tm_value(str(row.get("Value", ""))),
                        "contract_expiration": row.get("Contract expiration"),
                        "dob":                 row.get("DOB"),
                        "age":                 row.get("Age"),
                        "height_m":            row.get("Height (m)"),
                        "nationality":         row.get("Nationality"),
                        "citizenship":         json.dumps(row.get("Citizenship") or []),
                        "tm_position":         row.get("Position"),
                        "team":                row.get("Team"),
                        "last_club":           row.get("Last club"),
                        "joined":              row.get("Joined"),
                        "since":               row.get("Since"),
                        "league":              league,
                        "season":              season,
                        "_name_norm":          _norm_name(str(row.get("Name", ""))),
                    })

                    mvh = row.get("Market value history")
                    if isinstance(mvh, pd.DataFrame) and not mvh.empty:
                        for _, mr in mvh.iterrows():
                            mv_rows.append({
                                "tm_id": tm_id, "date": mr.get("date"),
                                "value_eur": mr.get("value"),
                                "league": league, "season": season,
                            })

                    th = row.get("Transfer history")
                    if isinstance(th, pd.DataFrame) and not th.empty:
                        for _, tr in th.iterrows():
                            transfer_rows.append({
                                "tm_id": tm_id, "tm_name": row.get("Name"),
                                **{k: tr.get(k) for k in
                                   ["Season", "Date", "Left", "Joined", "MV", "Fee"]},
                                "league": league, "season": season,
                            })

                    if (j + 1) % 100 == 0:
                        log.info(f"  [{j+1}/{len(player_links)}] scraped")

                except Exception as e:
                    log.warning(f"  ⚠️  {link}: {e}")

                time.sleep(sleep)

            if flat_rows:
                save_raw(pd.DataFrame(flat_rows), f"transfermarkt__{slug}")
                log.info(f"  ✅ {len(flat_rows)} players")
            if mv_rows:
                save_raw(pd.DataFrame(mv_rows), f"transfermarkt_mv_history__{slug}")
            if transfer_rows:
                save_raw(pd.DataFrame(transfer_rows), f"transfermarkt_transfers__{slug}")


# ══════════════════════════════════════════════════════════════════════════════
#  Capology helpers + collection
# ══════════════════════════════════════════════════════════════════════════════

def _cap_season(season: str, valid_seasons: list) -> str | None:
    """Map 'YYYY-YYYY' to whatever string Capology uses (e.g. '2024-25')."""
    start, end = season.split("-")
    s2, e2 = start[2:], end[2:]
    for fmt in [f"{s2}/{e2}", f"{start}/{e2}", f"{start}/{end}",
                f"{start}-{e2}", f"{start}-{end}"]:
        if fmt in valid_seasons:
            return fmt
    for vs in valid_seasons:
        if start in vs or s2 in vs:
            return vs
    return None


def _flatten_capology(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten Capology multi-index columns to snake_case strings."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    cols = []
    for l0, l1 in df.columns:
        l0c = l0.strip().lower().replace(" ", "_")
        l1c = l1.strip().lower().replace(" ", "_")
        if l1c and l1c != l0c:
            cols.append(f"{l1c}__{l0c}".strip("_"))
        else:
            cols.append(l0c)
    df = df.copy()
    df.columns = cols
    return df


def collect_capology(leagues=None, seasons=None, currency="eur"):
    """Scrape player wages from Capology for all supported leagues."""
    sys.path.insert(0, str(Path(__file__).parent / "src"))
    try:
        from ScraperFC import Capology
    except ImportError as e:
        log.error(f"Capology import error (Selenium required): {e}")
        return
    from ScraperFC.utils import get_module_comps

    all_cap = list(get_module_comps("CAPOLOGY").keys())
    leagues  = leagues or all_cap
    seasons  = seasons or SEASONS
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    for league in leagues:
        if league not in all_cap:
            log.info(f"  Capology: {league!r} not supported, skipping")
            continue

        try:
            valid = Capology().get_valid_seasons(league)
        except Exception as e:
            log.warning(f"  Capology get_valid_seasons failed for {league}: {e}")
            continue

        for season in seasons:
            cap_year = _cap_season(season, valid)
            if not cap_year:
                log.info(f"  Capology: {league} {season} not available")
                continue

            slug = f"{league.replace(' ','_')}__{season.replace('-','_')}"
            path = RAW_DIR / f"capology__{slug}.parquet"
            if path.exists():
                log.info(f"⏭️  capology__{slug}.parquet")
                continue

            log.info(f"📡 Capology: {league} {season} ({cap_year}) [{currency.upper()}]")
            try:
                df = Capology().scrape_salaries(cap_year, league, currency)
                df = _flatten_capology(df)
                df["league"]   = league
                df["season"]   = season
                df["currency"] = currency
                save_raw(df, f"capology__{slug}")
                log.info(f"  ✅ {len(df)} players")
            except Exception as e:
                log.error(f"  ❌ Capology {league} {season}: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Financial merge (Transfermarkt + Capology → unified CSV)
# ══════════════════════════════════════════════════════════════════════════════

def _fuzzy_tm_fill(unmatched: pd.DataFrame, tm_lookup: pd.DataFrame,
                   tm_cols: list[str], threshold: int = 85) -> pd.DataFrame:
    """
    For rows where the exact name merge missed, try rapidfuzz WRatio within
    the same league+season bucket.  Only accepts matches >= threshold (0-100).
    Short names (<= 5 chars) require >= 95 to avoid false positives like
    'Kepa' matching 'Kepa Arrizabalaga' but not 'Igor Thiago' matching 'Thiago'.
    Returns a DataFrame with the same index as unmatched, filled where possible.
    """
    from rapidfuzz import process, fuzz

    filled = unmatched.copy()

    for (league, season), group in unmatched.groupby(["league", "season"]):
        bucket = tm_lookup[(tm_lookup["league"] == league) &
                           (tm_lookup["season"] == season)]
        if bucket.empty:
            continue
        tm_names  = bucket["_name_norm"].tolist()
        tm_rows   = bucket.set_index("_name_norm")

        for idx, row in group.iterrows():
            query = row["_name_norm"]
            cutoff = 95 if len(query) <= 5 else threshold
            result = process.extractOne(
                query, tm_names,
                scorer=fuzz.WRatio,
                score_cutoff=cutoff,
            )
            if result is None:
                continue
            best_name, score, _ = result
            for col in tm_cols:
                if col in tm_rows.columns:
                    filled.at[idx, col] = tm_rows.at[best_name, col]

    return filled


def merge_financial_data(unified: pd.DataFrame) -> pd.DataFrame:
    """Add market value, contract, wages to unified DataFrame from TM + Capology."""
    unified = unified.copy()  # defragment before adding columns
    unified["_name_norm"] = unified["player"].apply(_norm_name)

    # ── Transfermarkt ─────────────────────────────────────────────────────────
    # Pass 1: exact name match on name+league+season
    # Pass 2: rapidfuzz WRatio fuzzy match for what's still unmatched
    tm_files = [f for f in sorted(RAW_DIR.glob("transfermarkt__*.parquet"))
                if "mv_history" not in f.name and "transfers" not in f.name]
    if tm_files:
        tm_frames = []
        for f in tm_files:
            try:
                tm_frames.append(pd.read_parquet(f))
            except Exception as e:
                log.warning(f"Could not load {f.name}: {e}")
        if tm_frames:
            tm_df = pd.concat(tm_frames, ignore_index=True)
            if "_name_norm" not in tm_df.columns:
                tm_df["_name_norm"] = tm_df["tm_name"].apply(_norm_name)
            tm_cols = [c for c in
                       ["tm_id", "market_value_eur", "contract_expiration",
                        "height_m", "nationality", "citizenship", "tm_position"]
                       if c in tm_df.columns]
            tm_lookup = (tm_df[["_name_norm", "league", "season"] + tm_cols]
                         .sort_values("season", ascending=False)
                         .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first"))

            # Pass 0 — manual overrides (remap unified name → TM name before merge)
            for (nn, league_name), tm_nn in MANUAL_TM_OVERRIDES.items():
                mask = (unified["_name_norm"] == nn) & (unified["league"] == league_name)
                if mask.any():
                    unified.loc[mask, "_name_norm"] = tm_nn

            # Pass 1 — exact
            unified = unified.merge(tm_lookup, on=["_name_norm", "league", "season"], how="left")
            exact_matched = unified["tm_id"].notna().sum() if "tm_id" in unified.columns else 0

            # Pass 2 — fuzzy for remaining unmatched rows
            unmatched_mask = unified["tm_id"].isna()
            if unmatched_mask.any():
                filled = _fuzzy_tm_fill(
                    unified[unmatched_mask][["_name_norm", "league", "season"] + tm_cols],
                    tm_lookup, tm_cols,
                )
                for col in tm_cols:
                    if col in filled.columns:
                        unified.loc[unmatched_mask, col] = filled[col].values

            total_matched = unified["tm_id"].notna().sum() if "tm_id" in unified.columns else 0
            fuzzy_matched = total_matched - exact_matched
            log.info(f"  TM merge: {total_matched}/{len(unified)} matched "
                     f"({exact_matched} exact + {fuzzy_matched} fuzzy)")

    # ── Capology ──────────────────────────────────────────────────────────────
    cap_files = sorted(RAW_DIR.glob("capology__*.parquet"))
    if cap_files:
        cap_frames = []
        for f in cap_files:
            try:
                cap_frames.append(pd.read_parquet(f))
            except Exception as e:
                log.warning(f"Could not load {f.name}: {e}")
        if cap_frames:
            cap_df = pd.concat(cap_frames, ignore_index=True)
            # Find player name column (first non-league/season text col)
            name_col = next(
                (c for c in cap_df.columns
                 if c not in {"league", "season", "currency"}
                 and cap_df[c].dtype == object),
                None,
            )
            if name_col:
                cap_df["_name_norm"] = cap_df[name_col].apply(_norm_name)
                wage_cols = [c for c in cap_df.columns if any(
                    kw in c.lower() for kw in ["weekly", "annual", "gross", "wage", "salary"]
                )]
                log.info(f"  Capology wage columns: {wage_cols}")
                if wage_cols:
                    cap_merge = (
                        cap_df[["_name_norm", "league", "season"] + wage_cols]
                        .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first")
                    )
                    unified = unified.merge(
                        cap_merge, on=["_name_norm", "league", "season"], how="left"
                    )
                    log.info(f"  Capology merged wage columns: {wage_cols}")

    unified = unified.drop(columns=["_name_norm"], errors="ignore")
    return unified


# ══════════════════════════════════════════════════════════════════════════════
#  Load helpers
# ══════════════════════════════════════════════════════════════════════════════

_NON_FBREF_PREFIXES = (
    "understat__", "understat_league_table__", "understat_match_info__",
    "understat_match_shots__", "understat_rosters__",
    "sofascore__", "sofascore_match_",
    "clubelo__",
    "transfermarkt__", "transfermarkt_mv_history__", "transfermarkt_transfers__",
    "capology__",
)


def load_all_fbref_raw() -> pd.DataFrame:
    frames = []
    for f in sorted(RAW_DIR.glob("*.parquet")):
        if f.name.startswith(_NON_FBREF_PREFIXES):
            continue
        try:
            frames.append(pd.read_parquet(f))
        except Exception as e:
            log.warning(f"Could not load {f.name}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_all_understat_raw() -> pd.DataFrame:
    frames = []
    for f in sorted(RAW_DIR.glob("understat__*.parquet")):
        try:
            frames.append(pd.read_parquet(f))
        except Exception as e:
            log.warning(f"Could not load {f.name}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_all_sofascore_raw() -> pd.DataFrame:
    frames = []
    for f in sorted(RAW_DIR.glob("sofascore__*.parquet")):
        try:
            frames.append(pd.read_parquet(f))
        except Exception as e:
            log.warning(f"Could not load {f.name}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


# ══════════════════════════════════════════════════════════════════════════════
#  Build helpers
# ══════════════════════════════════════════════════════════════════════════════

def _build_base_from_understat(us_df: pd.DataFrame) -> pd.DataFrame:
    """Build one row per (player, league, season) from Understat data."""
    us_dedup = (
        us_df.sort_values("us_minutes", ascending=False)
        .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first")
        .copy()
    )
    rename = {
        "id": "understat_id", "player": "player", "us_team": "team",
        "us_pos": "pos", "us_games": "games", "us_minutes": "minutes",
        "us_goals": "goals", "us_assists": "assists", "us_shots": "shots",
        "us_key_passes": "key_passes", "us_yellow_cards": "yellow_cards",
        "us_red_cards": "red_cards", "us_npg": "npg",
        "league": "league", "season": "season",
        "xg": "xg", "xag": "xag", "npxg": "npxg",
        "xg_chain": "xg_chain", "xg_buildup": "xg_buildup",
    }
    cols = {k: v for k, v in rename.items() if k in us_dedup.columns}
    base = us_dedup[list(cols)].rename(columns=cols).copy()
    for col in ["games","minutes","goals","assists","shots","key_passes",
                "yellow_cards","red_cards","npg","xg","xag","npxg",
                "xg_chain","xg_buildup"]:
        if col in base.columns:
            base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0)
    base["ninety_s"] = (base["minutes"] / 90).round(2)
    return base


def _build_base_from_sofascore(ss_df: pd.DataFrame) -> pd.DataFrame:
    """
    Build one row per (player, league, season) from SofaScore data.
    Used for non-Big5 leagues that have no Understat coverage.
    """
    if "_name_norm" not in ss_df.columns:
        ss_df = ss_df.copy()
        ss_df["_name_norm"] = ss_df["player"].apply(_norm_name)

    ss_dedup = (
        ss_df.sort_values("minutes" if "minutes" in ss_df.columns else ss_df.columns[0],
                          ascending=False)
        .drop_duplicates(subset=["_name_norm", "league", "season"], keep="first")
        .copy()
    )
    return ss_dedup.drop(columns=["_name_norm", "sofascore_team_id"], errors="ignore")


def _merge_understat_into(unified: pd.DataFrame, us_df: pd.DataFrame) -> pd.DataFrame:
    """Merge Understat xG/xA columns into an existing FBref-based unified frame."""
    if us_df.empty:
        return unified
    log.info(f"  Merging Understat: {len(us_df)} records, {us_df['league'].nunique()} leagues")
    unified["_name_norm"] = unified["player"].apply(_norm_name)
    if "_name_norm" not in us_df.columns:
        us_df["_name_norm"] = us_df["player"].apply(_norm_name)

    us_cols = ["xg","xag","npxg","xg_chain","xg_buildup","us_key_passes","us_npg","id"]
    us_merge = (
        us_df[["_name_norm","league","season"] + [c for c in us_cols if c in us_df.columns]]
        .sort_values("xg", ascending=False)
        .drop_duplicates(subset=["_name_norm","league","season"], keep="first")
    )
    unified = unified.merge(us_merge, on=["_name_norm","league","season"], how="left")

    if "us_key_passes" in unified.columns:
        if "key_passes" not in unified.columns:
            unified = unified.rename(columns={"us_key_passes": "key_passes"})
        else:
            mask = unified["key_passes"].isna() | (unified["key_passes"] == 0)
            unified.loc[mask, "key_passes"] = unified.loc[mask, "us_key_passes"]
            unified = unified.drop(columns=["us_key_passes"])
    if "us_npg" in unified.columns:
        unified = unified.rename(columns={"us_npg": "npg"})
    if "id" in unified.columns:
        unified = unified.rename(columns={"id": "understat_id"})
    unified = unified.drop(columns=["_name_norm"], errors="ignore")

    matched = (unified["xg"].fillna(0) > 0).sum()
    log.info(f"  xG coverage: {matched}/{len(unified)} ({100*matched//len(unified) if unified.shape[0] else 0}%)")
    return unified


def _merge_sofascore_into(unified: pd.DataFrame, ss_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge SofaScore stats into the unified DataFrame.

    Strategy:
    - For leagues already in unified (Big5 etc.): merge in NEW columns only.
      Understat xG/goals/assists/minutes are kept; don't overwrite them.
    - For leagues NOT in unified (Eredivisie, UCL, etc.): add new rows from SofaScore.
    """
    if ss_df.empty:
        return unified

    log.info(f"  Merging SofaScore: {len(ss_df)} records, {ss_df['league'].nunique()} leagues")

    if "_name_norm" not in ss_df.columns:
        ss_df = ss_df.copy()
        ss_df["_name_norm"] = ss_df["player"].apply(_norm_name)

    existing_leagues = set(unified["league"].unique())
    ss_existing = ss_df[ss_df["league"].isin(existing_leagues)].copy()
    ss_new      = ss_df[~ss_df["league"].isin(existing_leagues)].copy()

    merge_key = ["_name_norm", "league", "season"]

    # ── Part 1: merge new columns into existing rows ──────────────────────────
    if not ss_existing.empty:
        unified["_name_norm"] = unified["player"].apply(_norm_name)

        # Which columns do we already have (keep them, don't overwrite)?
        have_already = set(unified.columns) | UNDERSTAT_AUTHORITATIVE
        ss_new_cols  = [c for c in ss_existing.columns
                        if c not in have_already and c not in merge_key]

        # Always bring the rating + defensive/aerial stats regardless
        priority = [
            "sofascore_id", "sofascore_team_id", "sofascore_rating",
            "sofascore_rating_total", "sofascore_rating_count", "totw_appearances",
            "starts", "tackles", "tackles_won", "tackles_won_pct",
            "interceptions", "clearances", "blocked_shots", "outfield_blocks",
            "errors_leading_to_goal", "errors_leading_to_shot", "dribbled_past",
            "aerials_won", "aerials_won_pct", "aerials_lost",
            "dribbles_completed", "dribbles_pct", "dribbles_attempted",
            "ground_duels_won", "ground_duels_won_pct",
            "duels_won", "duels_won_pct", "duels_lost",
            "passes_total", "passes_completed", "passes_inaccurate", "pass_completion_pct",
            "passes_final_third", "passes_opp_half", "passes_own_half",
            "passes_opp_half_total", "passes_own_half_total",
            "long_balls_total", "long_balls_completed", "long_balls_pct",
            "crosses_total", "crosses_completed", "crosses_pct",
            "chipped_passes_total", "chipped_passes_completed",
            "pass_to_assist", "attempt_assists", "big_chances_created",
            "big_chances_missed", "goals_inside_box", "goals_outside_box",
            "goals_headed", "goals_left_foot", "goals_right_foot", "goals_penalty",
            "goals_freekick", "own_goals", "goals_assists", "hit_woodwork",
            "shots_inside_box", "shots_outside_box", "shots_on_target", "shots_off_target",
            "blocked_shots", "shots_set_piece",
            "goal_conversion_pct", "scoring_frequency", "set_piece_conversion",
            "touches", "possession_lost", "possession_won_att_third",
            "dispossessed", "ball_recoveries", "fouls", "fouled", "offsides",
            "yellow_red_cards", "direct_red_cards",
            "pens_taken", "pens_conceded", "pens_won", "pen_conversion_pct",
            "pen_miss", "pen_post", "pen_on_target",
            "saves", "saves_caught", "saves_parried",
            "saves_inside_box", "saves_outside_box",
            "goals_conceded", "goals_conceded_inside_box", "goals_conceded_outside_box",
            "goals_prevented", "clean_sheets", "high_claims",
            "crosses_not_claimed", "punches", "runs_out", "runs_out_successful",
            "goal_kicks", "pens_saved", "pens_faced",
        ]
        bring = [c for c in priority if c in ss_existing.columns
                 and c not in have_already]
        # Also grab any other new columns we haven't listed
        bring += [c for c in ss_new_cols if c not in bring]

        if bring:
            ss_merge = (
                ss_existing[merge_key + bring]
                .sort_values("sofascore_rating" if "sofascore_rating" in ss_existing.columns
                             else bring[0], ascending=False)
                .drop_duplicates(subset=merge_key, keep="first")
            )
            unified = unified.merge(ss_merge, on=merge_key, how="left")

        unified = unified.drop(columns=["_name_norm"], errors="ignore")

    # ── Part 2: add rows for new leagues (Eredivisie, UCL, etc.) ─────────────
    if not ss_new.empty:
        new_leagues = sorted(ss_new["league"].unique())
        log.info(f"  Adding {len(new_leagues)} new leagues from SofaScore: {new_leagues}")
        new_rows = _build_base_from_sofascore(ss_new)
        # Ensure no duplicate columns before concat
        new_rows = new_rows.loc[:, ~new_rows.columns.duplicated(keep="first")]
        unified  = unified.loc[:, ~unified.columns.duplicated(keep="first")]
        unified  = pd.concat([unified, new_rows], ignore_index=True)
        log.info(f"  Unified now {len(unified)} rows after adding SofaScore leagues")

    return unified


def _finalize_and_save(unified: pd.DataFrame) -> pd.DataFrame:
    """Compute derived columns, fill NaN, save CSV + manifest."""
    # Defragment before adding many new columns (avoids PerformanceWarning)
    unified = unified.copy()

    # Numeric fill
    numeric_cols = unified.select_dtypes(include="number").columns
    unified[numeric_cols] = unified[numeric_cols].fillna(0)

    # ninety_s
    if "ninety_s" not in unified.columns and "minutes" in unified.columns:
        unified["ninety_s"] = (unified["minutes"] / 90).round(2)

    n90 = unified.get("ninety_s", pd.Series(dtype=float)).replace(0, float("nan"))

    per90_pairs = [
        ("goals",               "goals_per90"),
        ("assists",             "assists_per90"),
        ("npg",                 "npg_per90"),
        ("xg",                  "xg_per90"),
        ("xag",                 "xag_per90"),
        ("npxg",                "npxg_per90"),
        ("xg_chain",            "xg_chain_per90"),
        ("xg_buildup",          "xg_buildup_per90"),
        ("shots",               "shots_per90"),
        ("key_passes",          "key_passes_per90"),
        ("tackles_won",         "tackles_won_per90"),
        ("interceptions",       "interceptions_per90"),
        ("big_chances_created", "big_chances_created_per90"),
        ("dribbles_completed",  "dribbles_per90"),
        ("progressive_passes",  "prog_passes_per90"),
        ("progressive_carries", "prog_carries_per90"),
        ("sca",                 "sca_per90"),
        ("gca",                 "gca_per90"),
    ]
    for src, dst in per90_pairs:
        if src in unified.columns and dst not in unified.columns:
            unified[dst] = (pd.to_numeric(unified[src], errors="coerce") / n90).round(3).fillna(0)

    # Aerial %
    if "aerials_won" in unified.columns and "aerials_lost" in unified.columns:
        if "aerials_won_pct" not in unified.columns or (unified["aerials_won_pct"] == 0).all():
            tot = unified["aerials_won"] + unified["aerials_lost"]
            unified["aerials_won_pct"] = (
                unified["aerials_won"] / tot.replace(0, float("nan")) * 100
            ).round(1).fillna(0)

    # xG overperformance
    if "goals" in unified.columns and "xg" in unified.columns:
        unified["xg_overperformance"] = (
            pd.to_numeric(unified["goals"], errors="coerce") -
            pd.to_numeric(unified["xg"],   errors="coerce")
        ).round(2).fillna(0)

    if "npg" in unified.columns and "npxg" in unified.columns:
        unified["npxg_overperformance"] = (
            pd.to_numeric(unified["npg"],  errors="coerce") -
            pd.to_numeric(unified["npxg"], errors="coerce")
        ).round(2).fillna(0)

    # Sort
    sort_cols = [c for c in ["league","season","team","player"] if c in unified.columns]
    if sort_cols:
        unified = unified.sort_values(sort_cols).reset_index(drop=True)

    # Save
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out = DATA_DIR / "unified_player_stats.csv"
    unified.to_csv(out, index=False)
    log.info(f"✅ Unified CSV: {len(unified)} rows × {len(unified.columns)} cols → {out}")

    manifest = {
        "columns":       list(unified.columns),
        "leagues":       sorted(unified["league"].unique().tolist()) if "league" in unified.columns else [],
        "seasons":       sorted(unified["season"].unique().tolist()) if "season" in unified.columns else [],
        "row_count":     len(unified),
        "xg_available":  bool("xg" in unified.columns and (unified["xg"] > 0).any()),
        "rating_available": bool("sofascore_rating" in unified.columns and
                                 (unified["sofascore_rating"] > 0).any()),
        "last_built_at": datetime.now(timezone.utc).isoformat(),
    }
    oldest = None
    if FRESHNESS_PATH.exists():
        try:
            fr = json.loads(FRESHNESS_PATH.read_text(encoding="utf-8"))
            for _k, meta in fr.items():
                ts = meta.get("fetched_at")
                if not ts:
                    continue
                try:
                    # fromisoformat accepts +00:00
                    t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                oldest = t if oldest is None or t < oldest else oldest
        except Exception as e:
            log.warning(f"Could not read freshness for manifest: {e}")
    manifest["oldest_source_fetched_at"] = oldest.isoformat() if oldest else None
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    log.info("📋 Manifest saved")
    return unified


# ══════════════════════════════════════════════════════════════════════════════
#  Main build
# ══════════════════════════════════════════════════════════════════════════════

def build_unified():
    """
    Merge all raw data into data/unified_player_stats.csv.

    Layer order (each adds/overrides):
      1. FBref   — basic stats, all leagues, if available
      2. Understat — xG/xA for Big 5 leagues (overrides FBref xG when present)
      3. SofaScore — 80+ stats for 37 leagues; adds rows for non-FBref leagues
    """
    log.info("🔨 Building unified player stats CSV …")

    fbref_df = load_all_fbref_raw()
    us_df    = load_all_understat_raw()
    ss_df    = load_all_sofascore_raw()

    if fbref_df.empty and us_df.empty and ss_df.empty:
        log.error("No raw data found. Run: python3 collect_data.py")
        return pd.DataFrame()

    key = ["player", "team", "league", "season"]

    # ── Layer 1: FBref base ──────────────────────────────────────────────────
    if not fbref_df.empty:
        cats = list(fbref_df["stat_category"].unique())
        log.info(f"  FBref categories: {sorted(cats)}")

        base_cats  = ["standard", "playing time", "misc"]
        other_cats = [c for c in cats if c not in base_cats]

        def dedup(df):
            if df.empty: return df
            df = df.copy()
            df["_score"] = df.notna().sum(axis=1)
            return (df.sort_values("_score", ascending=False)
                      .drop_duplicates(subset=key, keep="first")
                      .drop(columns=["_score"]))

        base_frames = [dedup(fbref_df[fbref_df["stat_category"]==c]
                             .drop(columns=["stat_category"], errors="ignore"))
                       for c in base_cats if c in cats]
        if not base_frames:
            base_frames = [dedup(fbref_df[fbref_df["stat_category"]==cats[0]]
                                 .drop(columns=["stat_category"], errors="ignore"))]

        unified = base_frames[0]
        for df in base_frames[1:]:
            new_cols = [c for c in df.columns if c not in unified.columns or c in key]
            unified  = unified.merge(df[new_cols], on=key, how="outer", suffixes=("","_dup"))
            unified  = unified[[c for c in unified.columns if not c.endswith("_dup")]]

        for cat in other_cats:
            cat_df = dedup(fbref_df[fbref_df["stat_category"]==cat]
                           .drop(columns=["stat_category","nation","pos","age","born",
                                          "player_id","team_id"], errors="ignore"))
            if cat_df.empty: continue
            new_cols = [c for c in cat_df.columns if c not in unified.columns or c in key]
            if len(new_cols) <= len(key): continue
            unified = unified.merge(cat_df[new_cols], on=key, how="left", suffixes=("","_dup"))
            unified = unified[[c for c in unified.columns if not c.endswith("_dup")]]
            log.info(f"  FBref '{cat}': {len(unified)} rows, {len(unified.columns)} cols")

    elif not us_df.empty:
        # No FBref — build base from Understat
        unified = _build_base_from_understat(us_df)
        us_df   = pd.DataFrame()  # already consumed

    elif not ss_df.empty:
        # No FBref, no Understat — build entirely from SofaScore
        log.info("  No FBref/Understat — building base from SofaScore only")
        unified = _build_base_from_sofascore(ss_df)
        ss_df   = pd.DataFrame()  # already consumed

    # ── Layer 2: Understat xG ────────────────────────────────────────────────
    if not us_df.empty:
        unified = _merge_understat_into(unified, us_df)

    # ── Layer 3: SofaScore ───────────────────────────────────────────────────
    if not ss_df.empty:
        unified = _merge_sofascore_into(unified, ss_df)

    # ── Layer 4: Financial data (Transfermarkt + Capology) ───────────────────
    unified = merge_financial_data(unified)

    return _finalize_and_save(unified)


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(
        description="Collect football stats — FBref + Understat + SofaScore + Transfermarkt + Capology"
    )
    p.add_argument("--leagues",                 nargs="*", help="Override league list")
    p.add_argument("--seasons",                 nargs="*", help="Seasons e.g. 2025-2026 2024-2025")
    p.add_argument("--stats",                   nargs="*", help="FBref stat categories")
    p.add_argument("--wait",                    type=int,   default=7,   help="FBref request delay (s)")
    p.add_argument("--sleep",                   type=float, default=0.5, help="Sleep between requests for TM / Understat matches")
    # Source flags
    p.add_argument("--fbref-only",              action="store_true")
    p.add_argument("--understat-only",          action="store_true", help="Run old Understat season stats only")
    p.add_argument("--understat-tables-only",   action="store_true", help="Run Understat league tables only (fast)")
    p.add_argument("--understat-matches-only",  action="store_true", help="Run Understat match shots + rosters only (~90 min)")
    p.add_argument("--sofascore-only",          action="store_true")
    p.add_argument("--sofascore-matches-only",  action="store_true",
                   help="SofaScore per-match shots / team / player / momentum only (~hours)")
    p.add_argument("--clubelo-only",            action="store_true",
                   help="ClubElo global ratings + fixtures (HTTP, seconds)")
    p.add_argument("--clubelo-date",            type=str, default=None,
                   help="YYYY-MM-DD for ClubElo global snapshot (default: today UTC)")
    p.add_argument("--force-matches",             action="store_true",
                   help="Re-fetch SofaScore match parquets even if all four exist")
    p.add_argument(
        "--parallel",
        type=int,
        default=1,
        metavar="N",
        help=(
            "SofaScore per-match: run up to N league-season jobs in parallel processes "
            "(default: 1). Safe with Bright Data — each job writes its own parquet files."
        ),
    )
    p.add_argument("--transfermarkt-only",      action="store_true", help="Run Transfermarkt player profiles only (~6 hrs)")
    p.add_argument("--capology-only",           action="store_true", help="Run Capology wages only (~2 hrs)")
    p.add_argument("--rebuild-only",            action="store_true", help="Skip scraping; rebuild unified CSV from raw files")
    # Skip flags
    p.add_argument("--no-fbref",                action="store_true")
    p.add_argument("--no-understat",            action="store_true")
    p.add_argument("--no-sofascore",            action="store_true")
    p.add_argument("--no-sofascore-matches",    action="store_true",
                   help="Skip SofaScore per-match collection on full runs")
    p.add_argument("--no-clubelo",              action="store_true", help="Skip ClubElo snapshot on full runs")
    p.add_argument("--no-transfermarkt",        action="store_true")
    p.add_argument("--no-capology",             action="store_true")
    args = p.parse_args()

    leagues = args.leagues or None
    seasons = args.seasons or SEASONS
    stats   = args.stats   or STAT_CATEGORIES

    # Single-source-only flags (mutually exclusive, first wins)
    only_flags = [
        ("fbref",             args.fbref_only),
        ("understat",         args.understat_only),
        ("understat_tables",  args.understat_tables_only),
        ("understat_matches", args.understat_matches_only),
        ("sofascore",         args.sofascore_only),
        ("sofascore_matches", args.sofascore_matches_only),
        ("clubelo",           args.clubelo_only),
        ("transfermarkt",     args.transfermarkt_only),
        ("capology",          args.capology_only),
    ]
    active_only = next((name for name, flag in only_flags if flag), None)

    def _run(source: str) -> bool:
        if active_only:
            return source == active_only
        skip_attr = f"no_{source.split('_')[0]}"  # e.g. no_transfermarkt
        return not getattr(args, skip_attr, False)

    print("=" * 70)
    print("  Football Data Collector")
    print("=" * 70)
    print(f"Seasons : {seasons}")
    if leagues:
        print(f"Leagues : {leagues}")
    print()

    if not args.rebuild_only:
        if _run("understat"):
            print("── Understat (season xG/xA, Big 5) ─────────────────────────────────")
            collect_understat(leagues=leagues, seasons=seasons)
            print()

        if _run("understat_tables"):
            print("── Understat league tables (xG, PPDA, Big 5) ───────────────────────")
            collect_understat_league_tables(leagues=leagues, seasons=seasons)
            print()

        if _run("understat_matches"):
            print("── Understat match shots + rosters (Big 5) ─────────────────────────")
            collect_understat_matches(leagues=leagues, seasons=seasons, sleep=args.sleep)
            print()

        if _run("sofascore"):
            print("── SofaScore (80+ stats, target leagues) ───────────────────────────")
            collect_sofascore(leagues=leagues, seasons=seasons)
            print()

        run_sofascore_matches = (
            active_only == "sofascore_matches"
            or (
                active_only is None
                and not args.no_sofascore_matches
                and not args.no_sofascore
            )
        )
        if run_sofascore_matches:
            print("── SofaScore per-match (shots, team stats, player stats, momentum) ─")
            pw = max(1, int(args.parallel))
            if pw > 1:
                print(f"Parallel workers : {pw}")
            collect_sofascore_matches(
                leagues=leagues,
                seasons=seasons,
                force=args.force_matches,
                parallel=pw,
            )
            print()

        run_clubelo = active_only == "clubelo" or (
            active_only is None and not args.no_clubelo
        )
        if run_clubelo:
            print("── ClubElo (ratings + fixtures) ─────────────────────────────────────")
            collect_clubelo(date=args.clubelo_date)
            print()

        if _run("fbref"):
            print("── FBref (basic stats, 8 leagues) ──────────────────────────────────")
            collect_fbref(leagues=leagues or FBREF_LEAGUES,
                          seasons=seasons, stat_categories=stats, wait_time=args.wait)
            print()

        if _run("transfermarkt"):
            print("── Transfermarkt (player profiles + MV, all supported leagues) ─────")
            collect_transfermarkt(leagues=leagues, seasons=seasons, sleep=args.sleep)
            print()

        if _run("capology"):
            print("── Capology (wages EUR, all supported leagues) ──────────────────────")
            collect_capology(leagues=leagues, seasons=seasons, currency="eur")
            print()

    # Only rebuild when: explicit --rebuild-only, full run (no active_only), or
    # a source that feeds unified CSV was just collected (not tables/matches/TM/Capology alone)
    supplementary_only = active_only in (
        "understat_tables", "understat_matches", "transfermarkt", "capology",
        "sofascore_matches", "clubelo",
    )
    if not supplementary_only:
        print("── Building unified CSV ─────────────────────────────────────────────")
        build_unified()
    else:
        print(f"── Skipping unified CSV rebuild (supplementary data only: {active_only}) ──")
        print("   Run: python3 collect_data.py --rebuild-only  to merge into unified CSV")
    print("\nDone! ✅")


if __name__ == "__main__":
    main()
