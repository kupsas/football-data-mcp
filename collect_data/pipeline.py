"""CLI orchestration: argparse + dispatch to collectors and unified build."""

from __future__ import annotations

import argparse

from collect_data.config import FBREF_LEAGUES, SEASONS, STAT_CATEGORIES
from collect_data.build.unified import build_unified
from collect_data.collectors.capology import collect_capology
from collect_data.collectors.clubelo import collect_clubelo
from collect_data.collectors.fbref import collect_fbref
from collect_data.collectors.sofascore import collect_sofascore
from collect_data.collectors.sofascore_matches import collect_sofascore_matches
from collect_data.collectors.transfermarkt import collect_transfermarkt
from collect_data.collectors.understat import (
    collect_understat,
    collect_understat_league_tables,
    collect_understat_matches,
)


def main() -> None:
    p = argparse.ArgumentParser(
        description="Collect football stats — FBref + Understat + SofaScore + Transfermarkt + Capology"
    )
    p.add_argument("--leagues",                 nargs="*", help="Override league list")
    p.add_argument("--seasons",                 nargs="*", help="Seasons e.g. 2025-2026 2024-2025")
    p.add_argument("--stats",                   nargs="*", help="FBref stat categories")
    p.add_argument("--wait",                    type=int,   default=7,   help="FBref request delay (s)")
    p.add_argument("--sleep",                   type=float, default=0.5, help="Sleep between requests for TM / Understat matches")
    p.add_argument("--export-csv",              action="store_true",
                   help="Also write unified_player_stats.csv alongside the Parquet build")
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
    p.add_argument("--rebuild-only",            action="store_true", help="Skip scraping; rebuild unified table from raw files")
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
        skip_attr = f"no_{source.split('_')[0]}"
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

    supplementary_only = active_only in (
        "understat_tables", "understat_matches", "transfermarkt", "capology",
        "sofascore_matches", "clubelo",
    )
    if not supplementary_only:
        print("── Building unified Parquet ─────────────────────────────────────────")
        build_unified(export_csv=args.export_csv)
    else:
        print(f"── Skipping unified rebuild (supplementary data only: {active_only}) ──")
        print("   Run: python3 -m collect_data --rebuild-only  to merge into unified table")
    print("\nDone! ✅")
