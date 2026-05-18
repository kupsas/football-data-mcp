"""Understat season stats, league tables, and match-level data (HTTP only)."""

from __future__ import annotations

import logging
import time

import pandas as pd
import requests

from collect_data.config import (
    SEASONS,
    UNDERSTAT_AJAX_BASE,
    UNDERSTAT_AJAX_HEADERS,
    UNDERSTAT_API,
    UNDERSTAT_HEADERS,
    UNDERSTAT_LEAGUES,
)
from collect_data.helpers import _norm_name, fbref_season_to_understat
from collect_data.storage import RAW_DIR, save_raw

log = logging.getLogger(__name__)


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
            us_year = fbref_season_to_understat(season)
            fname = f"understat__{league.replace(' ', '_')}__{season.replace('-', '_')}"
            raw_path = RAW_DIR / f"{fname}.parquet"
            if raw_path.exists():
                log.info(f"⏭️  {raw_path.name}")
                continue
            log.info(f"📡 Understat: {league} {season} (year={us_year})")
            try:
                resp = requests.post(
                    UNDERSTAT_API,
                    data={"league": code, "season": us_year},
                    headers=UNDERSTAT_HEADERS,
                    timeout=30,
                )
                resp.raise_for_status()
                payload = resp.json()
                if not payload.get("success"):
                    log.warning(f"  Understat error: {payload}")
                    continue
                df = pd.DataFrame(payload["players"]).rename(
                    columns={
                        "player_name": "player",
                        "time": "us_minutes",
                        "games": "us_games",
                        "goals": "us_goals",
                        "xG": "xg",
                        "assists": "us_assists",
                        "xA": "xag",
                        "shots": "us_shots",
                        "key_passes": "us_key_passes",
                        "yellow_cards": "us_yellow_cards",
                        "red_cards": "us_red_cards",
                        "position": "us_pos",
                        "team_title": "us_team",
                        "npg": "us_npg",
                        "npxG": "npxg",
                        "xGChain": "xg_chain",
                        "xGBuildup": "xg_buildup",
                    }
                )
                num = [
                    "us_minutes",
                    "us_games",
                    "us_goals",
                    "xg",
                    "us_assists",
                    "xag",
                    "us_shots",
                    "us_key_passes",
                    "us_yellow_cards",
                    "us_red_cards",
                    "us_npg",
                    "npxg",
                    "xg_chain",
                    "xg_buildup",
                ]
                for col in num:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                df["league"] = league
                df["season"] = season
                df["_name_norm"] = df["player"].apply(_norm_name)
                save_raw(df, fname)
                log.info(f"  ✅ {len(df)} players")
            except Exception as e:
                log.error(f"  ❌ Understat {league} {season}: {e}")
            time.sleep(2)


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
            slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
            overall_path = RAW_DIR / f"understat_league_table__{slug}__overall.parquet"
            if overall_path.exists():
                log.info(f"⏭️  understat_league_table__{slug}__overall.parquet")
                continue

            log.info(f"📡 Understat league table: {league} {season}")
            data = _us_ajax_get(
                f"getLeagueData/{code}/{us_year}",
                referer=f"https://understat.com/league/{code}/{us_year}",
            )
            if not data:
                log.warning(f"  ⚠️  No data for {league} {season}")
                continue

            teams = data.get("teams", {})
            rows = []
            for t in teams.values():
                for match in t.get("history", []):
                    rows.append(
                        {
                            "team_id": t["id"],
                            "team": t["title"],
                            "h_a": match.get("h_a"),
                            "xG": float(match.get("xG", 0)),
                            "xGA": float(match.get("xGA", 0)),
                            "npxG": float(match.get("npxG", 0)),
                            "npxGA": float(match.get("npxGA", 0)),
                            "ppda_att": match.get("ppda", {}).get("att", 0),
                            "ppda_def": match.get("ppda", {}).get("def", 1),
                            "ppda_allowed_att": match.get("ppda_allowed", {}).get("att", 0),
                            "ppda_allowed_def": match.get("ppda_allowed", {}).get("def", 1),
                            "deep": int(match.get("deep", 0)),
                            "deep_allowed": int(match.get("deep_allowed", 0)),
                            "goals": int(match.get("scored", 0)),
                            "goals_against": int(match.get("missed", 0)),
                            "xpts": float(match.get("xpts", 0)),
                            "wins": int(match.get("wins", 0)),
                            "draws": int(match.get("draws", 0)),
                            "losses": int(match.get("loses", 0)),
                            "pts": int(match.get("pts", 0)),
                            "npxGD": float(match.get("npxGD", 0)),
                            "date": match.get("date"),
                        }
                    )
            if not rows:
                continue

            df = pd.DataFrame(rows)
            agg = {
                "wins": "sum",
                "draws": "sum",
                "losses": "sum",
                "pts": "sum",
                "goals": "sum",
                "goals_against": "sum",
                "xG": "sum",
                "xGA": "sum",
                "npxG": "sum",
                "npxGA": "sum",
                "npxGD": "sum",
                "xpts": "sum",
                "deep": "sum",
                "deep_allowed": "sum",
                "ppda_att": "sum",
                "ppda_def": "sum",
                "ppda_allowed_att": "sum",
                "ppda_allowed_def": "sum",
            }

            def _build_table(sub):
                t = sub.groupby(["team_id", "team"], as_index=False).agg(agg)
                t["M"] = t["wins"] + t["draws"] + t["losses"]
                t["PPDA"] = (t["ppda_att"] / t["ppda_def"].replace(0, 1)).round(2)
                t["OPPDA"] = (t["ppda_allowed_att"] / t["ppda_allowed_def"].replace(0, 1)).round(2)
                t = t.drop(columns=["ppda_att", "ppda_def", "ppda_allowed_att", "ppda_allowed_def"])
                t["league"] = league
                t["season"] = season
                return t.sort_values("pts", ascending=False).reset_index(drop=True)

            save_raw(_build_table(df), f"understat_league_table__{slug}__overall")
            save_raw(_build_table(df[df["h_a"] == "h"]), f"understat_league_table__{slug}__home")
            save_raw(_build_table(df[df["h_a"] == "a"]), f"understat_league_table__{slug}__away")
            log.info(f"  ✅ {len(teams)} teams")
            time.sleep(1)


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
            slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
            shots_path = RAW_DIR / f"understat_match_shots__{slug}.parquet"
            if shots_path.exists():
                log.info(f"⏭️  understat_match_shots__{slug}.parquet")
                continue

            log.info(f"📡 Understat matches: {league} {season} — fetching match IDs")
            league_data = _us_ajax_get(
                f"getLeagueData/{code}/{us_year}",
                referer=f"https://understat.com/league/{code}/{us_year}",
            )
            if not league_data:
                log.warning(f"  ⚠️  No league data for {league} {season}")
                continue

            completed = [m for m in league_data.get("dates", []) if m.get("isResult")]
            log.info(f"  {len(completed)} completed matches to fetch")

            info_path = RAW_DIR / f"understat_match_info__{slug}.parquet"
            if not info_path.exists():
                info_rows = []
                for m in completed:
                    info_rows.append(
                        {
                            "match_id": int(m["id"]),
                            "home_team": m["h"]["title"],
                            "away_team": m["a"]["title"],
                            "home_team_id": m["h"]["id"],
                            "away_team_id": m["a"]["id"],
                            "home_goals": int(m["goals"]["h"]),
                            "away_goals": int(m["goals"]["a"]),
                            "home_xg": float(m["xG"]["h"]),
                            "away_xg": float(m["xG"]["a"]),
                            "datetime": m.get("datetime"),
                            "league": league,
                            "season": season,
                        }
                    )
                save_raw(pd.DataFrame(info_rows), f"understat_match_info__{slug}")

            all_shots, all_rosters = [], []
            for i, match in enumerate(completed, 1):
                mid = match["id"]
                if i % 50 == 0:
                    log.info(f"  [{i}/{len(completed)}] fetching match {mid}")

                mdata = _us_ajax_get(f"getMatchData/{mid}", referer=f"https://understat.com/match/{mid}")
                if not mdata:
                    time.sleep(sleep)
                    continue

                shots_raw = mdata.get("shots", {})
                for h_a, shot_list in shots_raw.items():
                    for s in shot_list if isinstance(shot_list, list) else []:
                        all_shots.append(
                            {
                                "match_id": int(mid),
                                "player_id": s.get("player_id"),
                                "player": s.get("player"),
                                "h_a": h_a,
                                "minute": s.get("minute"),
                                "result": s.get("result"),
                                "X": float(s.get("X", 0)),
                                "Y": float(s.get("Y", 0)),
                                "xG": float(s.get("xG", 0)),
                                "situation": s.get("situation"),
                                "shot_type": s.get("shotType"),
                                "player_assisted": s.get("player_assisted"),
                                "last_action": s.get("lastAction"),
                                "league": league,
                                "season": season,
                            }
                        )

                for h_a, players in mdata.get("rosters", {}).items():
                    for entry in players.values() if isinstance(players, dict) else []:
                        all_rosters.append(
                            {
                                "match_id": int(mid),
                                "roster_entry_id": entry.get("id"),
                                "player_id": entry.get("player_id"),
                                "player": entry.get("player"),
                                "team_id": entry.get("team_id"),
                                "position": entry.get("position"),
                                "h_a": h_a,
                                "minutes": int(entry.get("time", 0)),
                                "goals": int(entry.get("goals", 0)),
                                "own_goals": int(entry.get("own_goals", 0)),
                                "shots": int(entry.get("shots", 0)),
                                "xG": float(entry.get("xG", 0)),
                                "assists": int(entry.get("assists", 0)),
                                "xA": float(entry.get("xA", 0)),
                                "xGChain": float(entry.get("xGChain", 0)),
                                "xGBuildup": float(entry.get("xGBuildup", 0)),
                                "yellow_card": int(entry.get("yellow_card", 0)),
                                "red_card": int(entry.get("red_card", 0)),
                                "key_passes": int(entry.get("key_passes", 0)),
                                "roster_in": entry.get("roster_in"),
                                "roster_out": entry.get("roster_out"),
                                "position_order": entry.get("positionOrder"),
                                "league": league,
                                "season": season,
                            }
                        )

                time.sleep(sleep)

            if all_shots:
                save_raw(pd.DataFrame(all_shots), f"understat_match_shots__{slug}")
            if all_rosters:
                save_raw(pd.DataFrame(all_rosters), f"understat_rosters__{slug}")
            log.info(f"  ✅ {len(all_shots)} shots, {len(all_rosters)} roster entries")
