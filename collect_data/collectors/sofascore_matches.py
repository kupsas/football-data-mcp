"""SofaScore per-match packs (shots, team stats, player stats, momentum)."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from collect_data.config import SEASONS, SOFASCORE_SEASONS, SOFASCORE_TARGET_LEAGUES
from collect_data.helpers import _silence_bota_noise, _ss_retry
from collect_data.storage import RAW_DIR, repo_root, save_raw, sofa_match_checkpoint_flush

log = logging.getLogger(__name__)

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
    *,
    last_match_date: str | None = None,
    total_finished: int | None = None,
) -> None:
    """
    Flush accumulated in-memory data to partial parquet files and write the
    checkpoint JSON.  Called every FLUSH_EVERY matches so a crashed run can
    resume from the last flush point rather than starting over.
    """
    sofa_match_checkpoint_flush(
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
        last_match_date=last_match_date,
        total_finished=total_finished,
    )


def _sofascore_match_pack_fully_done(
    force: bool,
    p_shots: Path,
    p_team: Path,
    p_play: Path,
    p_mom: Path,
    p_ckpt: Path,
    season: str | None = None,
) -> bool:
    """
    Return True only when a league-season SofaScore match job can be skipped.

    Checkpoint flushes write all four parquet files every FLUSH_EVERY matches, so
    "all four exist" does **not** mean the season is finished — an in-progress run
    always has ``sofascore_match_checkpoint__*.json`` alongside those files until
    the worker removes it after the final save.
    """
    if force:
        return False
    if not (
        p_shots.exists()
        and p_team.exists()
        and p_play.exists()
        and p_mom.exists()
    ):
        return False
    if p_ckpt.exists():
        return False
    # ``season is None`` = legacy callers / tests (treat as finished when files exist).
    if season is not None and season == SEASONS[0]:
        return False
    return True


def _discover_sofascore_match_jobs(
    leagues: list[str] | None,
    seasons: list[str] | None,
    sleep_between_matches: float,
    force: bool,
) -> list[tuple[str, str, float, bool]]:
    """
    Build the list of (league, season, sleep_between_matches, force) tuples that
    still need scraping.  Skips unsupported leagues, unavailable seasons, and
    league-seasons that are fully finished (four parquets and no checkpoint),
    unless force=True.
    """
    sys.path.insert(0, str(repo_root() / "src"))
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
            p_ckpt = RAW_DIR / f"sofascore_match_checkpoint__{slug}.json"
            if _sofascore_match_pack_fully_done(
                force, p_shots, p_team, p_play, p_mom, p_ckpt, season
            ):
                log.info(f"⏭️  SofaScore match pack complete for {slug}")
                continue
            jobs.append((league, season, sleep_between_matches, force))
    return jobs


def _sofascore_match_started_iso(m: dict) -> str | None:
    """Best-effort kickoff time from a SofaScore ``events`` dict (UTC ISO)."""
    ts = m.get("startTimestamp")
    if not isinstance(ts, (int, float)) or ts <= 0:
        return None
    secs = float(ts) / 1000.0 if ts > 1_000_000_000_000 else float(ts)
    return datetime.fromtimestamp(secs, tz=timezone.utc).isoformat()


def _sofascore_ckpt_flush_meta(
    done_ids: set[int], finished_by_id: dict[int, dict], total_finished: int
) -> tuple[str | None, int]:
    """Latest ISO start time among scraped IDs + API finished count."""
    dates: list[str] = []
    for mid in done_ids:
        mm = finished_by_id.get(mid)
        if not mm:
            continue
        iso = _sofascore_match_started_iso(mm)
        if iso:
            dates.append(iso)
    last = max(dates) if dates else None
    return last, int(total_finished)


def _sofascore_bootstrap_done_ids_from_disk(
    p_shots: Path, p_team: Path, p_play: Path, p_mom: Path
) -> set[int]:
    """Rebuild ``done_ids`` from existing match parquets (weekly incremental, no JSON)."""
    ids: set[int] = set()
    for p in (p_shots, p_team, p_play, p_mom):
        if not p.exists():
            continue
        try:
            df = pd.read_parquet(p, columns=["match_id"])
            ids.update(df["match_id"].dropna().astype(int).tolist())
        except Exception:
            continue
    return ids


def _sofascore_match_season_worker(
    job: tuple[str, str, float, bool],
) -> None:
    """
    Scrape one league+season match pack (four parquets).  Top-level so it can be
    pickled for ProcessPoolExecutor (macOS spawn).
    """
    league, season, sleep_between_matches, force = job
    pfx = f"[{league} | {season}]"

    sys.path.insert(0, str(repo_root() / "src"))
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

    if _sofascore_match_pack_fully_done(force, p_shots, p_team, p_play, p_mom, p_ckpt, season):
        log.info(f"{pfx}  ⏭️  match pack already complete")
        return

    done_ids: set[int] = set()
    if force:
        _ckpt_flush(
            slug,
            set(),
            [],
            [],
            [],
            [],
            p_shots,
            p_team,
            p_play,
            p_mom,
            p_ckpt,
            last_match_date=None,
            total_finished=0,
        )
        log.info(f"{pfx}  🔁 force-matches: checkpoint reset (was from any prior run)")
    elif p_ckpt.exists():
        try:
            raw_ck = json.loads(p_ckpt.read_text(encoding="utf-8"))
            done_ids = set(int(x) for x in raw_ck.get("done_ids", []))
        except Exception:
            pass
    elif (
        season == SEASONS[0]
        and p_shots.exists()
        and p_team.exists()
        and p_play.exists()
        and p_mom.exists()
    ):
        done_ids = _sofascore_bootstrap_done_ids_from_disk(p_shots, p_team, p_play, p_mom)
        if done_ids:
            log.info(
                f"{pfx}  📂 incremental: {len(done_ids)} match ids loaded from existing parquets"
            )

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
    finished_by_id: dict[int, dict] = {int(m["id"]): m for m in finished}
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

            # global_idx = position in SofaScore's finished list for the season (not
            # "how many matches scraped this session").  idx counts this run's queue.
            global_idx = match_seq.get(mid, idx)
            log.info(
                f"{pfx}  [season {global_idx}/{len(finished)} · this run {idx}/{len(remaining)}] "
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

            # Always flush once after the first match of this segment so Ctrl+C
            # cannot leave a checkpoint from a *previous* run (see force-matches
            # reset above) or an empty on-disk state for tens of matches.
            if idx == 1 or idx % FLUSH_EVERY == 0:
                log.info(f"{pfx}  💾 checkpoint flush at {global_idx}/{len(finished)} …")
                lm, tf = _sofascore_ckpt_flush_meta(done_ids, finished_by_id, len(finished))
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
                    last_match_date=lm,
                    total_finished=tf,
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
    Writes four parquet files per league+season. Skips when all four exist **and** there is
    no checkpoint file (in-progress runs flush all four periodically). Use force=True to
    rebuild from scratch.

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
