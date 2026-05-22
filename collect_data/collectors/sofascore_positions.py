"""SofaScore per-match average positions (and optional heatmaps)."""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import pandas as pd

from collect_data.config import SEASONS, SOFASCORE_SEASONS, SOFASCORE_TARGET_LEAGUES
from collect_data.helpers import _silence_bota_noise, _ss_retry
from collect_data.storage import RAW_DIR, repo_root, save_raw

log = logging.getLogger(__name__)

# Proof-of-concept default: one league; expand via --leagues on the CLI.
SOFASCORE_POSITIONS_DEFAULT_LEAGUES = ["England Premier League"]

FLUSH_EVERY = 25


def _avg_positions_slim(df: pd.DataFrame, match_id: int, league: str, season: str) -> pd.DataFrame:
    """Normalize SofaScore average-positions API frame."""
    if df.empty:
        return pd.DataFrame()
    out = df.copy()
    if "id" in out.columns and "player_id" not in out.columns:
        out = out.rename(columns={"id": "player_id"})
    if "name" in out.columns and "player_name" not in out.columns:
        out = out.rename(columns={"name": "player_name"})
    if "averageX" in out.columns:
        out = out.rename(columns={"averageX": "average_x", "averageY": "average_y"})
    out["match_id"] = match_id
    out["league"] = league
    out["season"] = season
    cols = [
        "match_id",
        "player_id",
        "player_name",
        "team",
        "average_x",
        "average_y",
        "league",
        "season",
    ]
    for c in cols:
        if c not in out.columns:
            out[c] = None
    return out[cols]


def _ckpt_path(slug: str) -> Path:
    return RAW_DIR / f"sofascore_positions_checkpoint__{slug}.json"


def _positions_pack_done(
    force: bool,
    out_path: Path,
    ckpt_path: Path,
    season: str | None = None,
) -> bool:
    if force:
        return False
    if not out_path.exists():
        return False
    if ckpt_path.exists():
        return False
    if season is not None and season == SEASONS[0]:
        return False
    return True


def _sofascore_positions_worker(
    job: tuple[str, str, float, bool, bool],
) -> None:
    league, season, sleep_s, force, include_heatmaps = job
    pfx = f"[{league} | {season}]"

    sys.path.insert(0, str(repo_root() / "src"))
    from ScraperFC import Sofascore

    ss = Sofascore()
    ss_year = SOFASCORE_SEASONS.get(season)
    if not ss_year:
        log.info(f"{pfx}  unknown season mapping, skip")
        return

    slug = f"{league.replace(' ', '_')}__{season.replace('-', '_')}"
    p_avg = RAW_DIR / f"sofascore_avg_positions__{slug}.parquet"
    p_heat = RAW_DIR / f"sofascore_heatmaps__{slug}.parquet"
    p_ckpt = _ckpt_path(slug)

    if _positions_pack_done(force, p_avg, p_ckpt, season):
        log.info(f"{pfx}  ⏭️  average positions pack complete")
        return

    done_ids: set[int] = set()
    if force and p_ckpt.exists():
        p_ckpt.unlink()
    elif p_ckpt.exists():
        try:
            done_ids = set(int(x) for x in json.loads(p_ckpt.read_text())["done_ids"])
        except Exception:
            pass
    elif p_avg.exists():
        try:
            done_ids = set(
                pd.read_parquet(p_avg, columns=["match_id"])["match_id"]
                .dropna()
                .astype(int)
                .tolist()
            )
        except Exception:
            pass

    def _load_partial() -> pd.DataFrame:
        if p_avg.exists() and done_ids:
            try:
                return pd.read_parquet(p_avg)
            except Exception:
                pass
        return pd.DataFrame()

    all_avg: list[pd.DataFrame] = [df for df in [_load_partial()] if not df.empty]
    all_heat_rows: list[dict] = []

    log.info(f"{pfx}  📡 SofaScore average positions ({ss_year})")
    try:
        with _silence_bota_noise():
            match_dicts = _ss_retry(
                ss.get_match_dicts,
                ss_year,
                league,
                label=f"get_match_dicts({league})",
            )
    except Exception as e:
        log.error(f"{pfx}  get_match_dicts failed: {e}")
        return

    finished = [
        m
        for m in match_dicts
        if isinstance(m.get("status"), dict) and m["status"].get("type") == "finished"
    ]
    remaining = [m for m in finished if int(m["id"]) not in done_ids]
    log.info(f"{pfx}  {len(finished)} finished, {len(remaining)} to scrape")

    for idx, m in enumerate(remaining, 1):
        mid = int(m["id"])
        home = (m.get("homeTeam") or {}).get("name", "")
        away = (m.get("awayTeam") or {}).get("name", "")
        log.info(f"{pfx}  [{idx}/{len(remaining)}] {home} vs {away} (id={mid})")

        try:
            with _silence_bota_noise():
                adf = _ss_retry(
                    ss.scrape_player_average_positions,
                    mid,
                    label=f"avg_positions {mid}",
                )
            all_avg.append(_avg_positions_slim(adf, mid, league, season))
        except Exception as e:
            log.warning(f"{pfx}    avg positions {mid}: {e}")

        if include_heatmaps:
            try:
                with _silence_bota_noise():
                    heat = _ss_retry(ss.scrape_heatmaps, mid, label=f"heatmaps {mid}")
                for pname, meta in (heat or {}).items():
                    pid = meta.get("id")
                    for x, y in meta.get("heatmap") or []:
                        all_heat_rows.append({
                            "match_id": mid,
                            "player_id": pid,
                            "player_name": pname,
                            "touch_x": x,
                            "touch_y": y,
                            "league": league,
                            "season": season,
                        })
            except Exception as e:
                log.warning(f"{pfx}    heatmaps {mid}: {e}")

        done_ids.add(mid)
        time.sleep(sleep_s)

        if idx == 1 or idx % FLUSH_EVERY == 0:
            if all_avg:
                pd.concat(all_avg, ignore_index=True).to_parquet(p_avg, index=False)
            if all_heat_rows and include_heatmaps:
                pd.DataFrame(all_heat_rows).to_parquet(p_heat, index=False)
            p_ckpt.write_text(
                json.dumps({"slug": slug, "done_ids": sorted(done_ids)}),
                encoding="utf-8",
            )
            log.info(f"{pfx}  💾 checkpoint {len(done_ids)} matches")

    avg_df = pd.concat(all_avg, ignore_index=True) if all_avg else pd.DataFrame()
    save_raw(avg_df, f"sofascore_avg_positions__{slug}")
    if include_heatmaps and all_heat_rows:
        save_raw(pd.DataFrame(all_heat_rows), f"sofascore_heatmaps__{slug}")
    if p_ckpt.exists():
        p_ckpt.unlink()
    log.info(f"{pfx}  ✅ saved {len(avg_df)} average-position rows")


def collect_sofascore_positions(
    leagues=None,
    seasons=None,
    *,
    sleep_between_matches: float = 2.0,
    force: bool = False,
    include_heatmaps: bool = False,
    parallel: int = 1,
) -> None:
    """
    Scrape SofaScore average pitch positions per match (PoC: EPL by default).

    ``include_heatmaps`` adds ~22 API calls per match; off by default.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    use_leagues = leagues or SOFASCORE_POSITIONS_DEFAULT_LEAGUES
    use_seasons = seasons or SEASONS

    sys.path.insert(0, str(repo_root() / "src"))
    from ScraperFC.utils import get_module_comps

    all_ss = list(get_module_comps("SOFASCORE").keys())
    jobs: list[tuple[str, str, float, bool, bool]] = []
    for league in use_leagues:
        if league not in all_ss:
            log.info("  SofaScore positions: %r not supported, skip", league)
            continue
        for season in use_seasons:
            jobs.append((league, season, sleep_between_matches, force, include_heatmaps))

    if not jobs:
        log.info("SofaScore positions: no jobs")
        return

    log.info("SofaScore position jobs: %s (parallel=%s)", len(jobs), parallel)
    if parallel <= 1:
        for job in jobs:
            _sofascore_positions_worker(job)
        return

    import concurrent.futures

    with concurrent.futures.ProcessPoolExecutor(max_workers=parallel) as pool:
        futs = {pool.submit(_sofascore_positions_worker, j): j for j in jobs}
        for fut in concurrent.futures.as_completed(futs):
            try:
                fut.result()
            except Exception as exc:
                log.error("SofaScore positions job failed: %s — %s", futs[fut], exc)
