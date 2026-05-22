"""
DuckDB query layer for the soccer MCP server.

Supports two storage backends transparently:

- **Local** (default): DuckDB reads parquet files directly from ``data/raw/`` on disk
  via file-glob paths.  Zero copying, very fast.

- **R2** (``DATA_BACKEND=r2``): DuckDB's built-in ``httpfs`` extension is configured
  with your Cloudflare R2 credentials, then parquet views are created using
  ``s3://bucket/raw/<glob>`` paths.  DuckDB handles the S3 wire protocol natively —
  no pandas ``io.BytesIO`` round-trips, no full-file downloads into memory.

In both modes the public API (``init_db``, ``query``, ``query_scalar``, ``table_empty``,
``refresh``) is identical, so tools never need to know which backend is active.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from collect_data.storage import get_backend

log = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: duckdb.DuckDBPyConnection | None = None
_initialized = False


def _is_r2() -> bool:
    return os.getenv("DATA_BACKEND", "local").lower().strip() == "r2"


# ── Path / URI helpers ─────────────────────────────────────────────────────────

def _local_data_dir() -> Path:
    be = get_backend()
    data_dir = getattr(be, "data_dir", None)
    if data_dir is None:
        from collect_data.storage import DATA_DIR

        return DATA_DIR.resolve()
    return Path(data_dir).resolve()


def _local_glob_path(data_dir: Path, rel_glob: str) -> str:
    """Absolute file-system glob path for DuckDB read_parquet (forward slashes)."""
    return str((data_dir / rel_glob).as_posix())


def _r2_bucket() -> str:
    return os.environ["R2_BUCKET"]


def _r2_glob_uri(rel_glob: str) -> str:
    """S3-style URI for DuckDB httpfs: ``s3://bucket/raw/<glob>``."""
    return f"s3://{_r2_bucket()}/{rel_glob}"


def _configure_r2_httpfs(con: duckdb.DuckDBPyConnection) -> None:
    """
    Install + load DuckDB's httpfs extension and configure it for Cloudflare R2.

    R2 is S3-compatible but requires a custom endpoint and ``s3_url_style=path``
    (R2 does not support virtual-hosted-style bucket URLs).  The credentials are
    read from the same env-vars used by ``R2Backend``.
    """
    con.execute("INSTALL httpfs; LOAD httpfs;")
    endpoint_url = os.environ.get("R2_ENDPOINT_URL", "")
    # R2 endpoint looks like https://<account_id>.r2.cloudflarestorage.com
    # DuckDB s3_endpoint wants the host only (no scheme).
    host = endpoint_url.replace("https://", "").replace("http://", "").rstrip("/")
    con.execute(f"SET s3_endpoint='{host}';")
    con.execute(f"SET s3_access_key_id='{os.environ['R2_ACCESS_KEY_ID']}';")
    con.execute(f"SET s3_secret_access_key='{os.environ['R2_SECRET_ACCESS_KEY']}';")
    # R2 is always in the "auto" region for SDK purposes.
    con.execute("SET s3_region='auto';")
    # R2 requires path-style access (not virtual-hosted-style).
    con.execute("SET s3_url_style='path';")
    log.info("DuckDB httpfs configured for R2 endpoint: %s", host)


# Minimal empty schemas when no parquet files exist (keeps downstream SQL valid).
_EMPTY_VIEW_SQL: dict[str, str] = {
    "sofascore_match_player_stats": """
        SELECT CAST(NULL AS BIGINT) AS match_id, CAST(NULL AS BIGINT) AS player_id,
               CAST(NULL AS VARCHAR) AS player_name, CAST(NULL AS BIGINT) AS team_id,
               CAST(NULL AS VARCHAR) AS team_name, CAST(NULL AS BOOLEAN) AS is_home,
               CAST(NULL AS BOOLEAN) AS substitute, CAST(NULL AS DOUBLE) AS minutes_played,
               CAST(NULL AS DOUBLE) AS rating, CAST(NULL AS DOUBLE) AS xg,
               CAST(NULL AS DOUBLE) AS xgot, CAST(NULL AS DOUBLE) AS xa,
               CAST(NULL AS DOUBLE) AS goals, CAST(NULL AS DOUBLE) AS assists,
               CAST(NULL AS DOUBLE) AS total_shots, CAST(NULL AS DOUBLE) AS shots_on_target,
               CAST(NULL AS DOUBLE) AS total_passes, CAST(NULL AS DOUBLE) AS accurate_passes,
               CAST(NULL AS DOUBLE) AS touches, CAST(NULL AS DOUBLE) AS possession_lost,
               CAST(NULL AS DOUBLE) AS duels_won, CAST(NULL AS DOUBLE) AS duels_lost,
               CAST(NULL AS DOUBLE) AS fouls, CAST(NULL AS DOUBLE) AS was_fouled,
               CAST(NULL AS VARCHAR) AS league, CAST(NULL AS VARCHAR) AS season
        WHERE false
    """,
    "sofascore_match_team_stats": """
        SELECT CAST(NULL AS BIGINT) AS match_id, CAST(NULL AS VARCHAR) AS home_team,
               CAST(NULL AS VARCHAR) AS away_team, CAST(NULL AS VARCHAR) AS league,
               CAST(NULL AS VARCHAR) AS season, CAST(NULL AS VARCHAR) AS period,
               CAST(NULL AS DOUBLE) AS ball_possession_home,
               CAST(NULL AS DOUBLE) AS ball_possession_away,
               CAST(NULL AS DOUBLE) AS expected_goals_home,
               CAST(NULL AS DOUBLE) AS expected_goals_away,
               CAST(NULL AS DOUBLE) AS shots_on_target_home,
               CAST(NULL AS DOUBLE) AS shots_on_target_away,
               CAST(NULL AS DOUBLE) AS shots_off_target_home,
               CAST(NULL AS DOUBLE) AS shots_off_target_away,
               CAST(NULL AS DOUBLE) AS total_shots_home,
               CAST(NULL AS DOUBLE) AS total_shots_away,
               CAST(NULL AS DOUBLE) AS shots_inside_box_home,
               CAST(NULL AS DOUBLE) AS shots_inside_box_away,
               CAST(NULL AS DOUBLE) AS shots_outside_box_home,
               CAST(NULL AS DOUBLE) AS shots_outside_box_away,
               CAST(NULL AS DOUBLE) AS final_third_entries_home,
               CAST(NULL AS DOUBLE) AS final_third_entries_away,
               CAST(NULL AS DOUBLE) AS touches_in_opp_box_home,
               CAST(NULL AS DOUBLE) AS touches_in_opp_box_away,
               CAST(NULL AS DOUBLE) AS offsides_home, CAST(NULL AS DOUBLE) AS offsides_away,
               CAST(NULL AS DOUBLE) AS corner_kicks_home,
               CAST(NULL AS DOUBLE) AS corner_kicks_away,
               CAST(NULL AS DOUBLE) AS big_chances_home,
               CAST(NULL AS DOUBLE) AS big_chances_away
        WHERE false
    """,
    "sofascore_match_shots": """
        SELECT CAST(NULL AS BIGINT) AS match_id, CAST(NULL AS BIGINT) AS player_id,
               CAST(NULL AS VARCHAR) AS player_name, CAST(NULL AS BOOLEAN) AS is_home,
               CAST(NULL AS DOUBLE) AS minute, CAST(NULL AS VARCHAR) AS shot_type,
               CAST(NULL AS VARCHAR) AS situation, CAST(NULL AS VARCHAR) AS body_part,
               CAST(NULL AS DOUBLE) AS xg, CAST(NULL AS DOUBLE) AS xgot,
               CAST(NULL AS VARCHAR) AS goal_mouth_location,
               CAST(NULL AS DOUBLE) AS player_x, CAST(NULL AS DOUBLE) AS player_y,
               CAST(NULL AS DOUBLE) AS goal_mouth_x, CAST(NULL AS DOUBLE) AS goal_mouth_y,
               CAST(NULL AS VARCHAR) AS league, CAST(NULL AS VARCHAR) AS season
        WHERE false
    """,
}


def _parquet_path(data_dir: Path | None, rel_glob: str) -> str:
    """
    Return a path/URI string for DuckDB ``read_parquet``.

    - Local backend: absolute filesystem glob path.
    - R2 backend: ``s3://bucket/rel_glob``.
    """
    if _is_r2():
        return _r2_glob_uri(rel_glob)
    assert data_dir is not None
    return _local_glob_path(data_dir, rel_glob)


def _create_view_from_glob(
    con: duckdb.DuckDBPyConnection,
    view_name: str,
    data_dir: Path | None,
    rel_glob: str,
    *,
    basename_glob: str | None = None,
) -> None:
    """
    Create a parquet-backed view.

    Falls back to an empty typed stub if the backend reports no matching files
    (avoids DuckDB errors on first-run or partial data installs).
    """
    pattern = basename_glob or rel_glob.split("/")[-1]
    if not get_backend().list_raw_glob(pattern):
        stub = _EMPTY_VIEW_SQL.get(view_name)
        if stub:
            con.execute(f"CREATE OR REPLACE VIEW {view_name} AS {stub}")
        else:
            con.execute(
                f"CREATE OR REPLACE VIEW {view_name} AS "
                f"SELECT CAST(NULL AS INTEGER) AS _empty WHERE false"
            )
        log.debug("View %s: empty stub (no files for %s)", view_name, pattern)
        return
    path = _parquet_path(data_dir, rel_glob)
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view_name} AS
        SELECT * FROM read_parquet('{path}', union_by_name=true)
        """
    )


_RAW_FAMILIES: list[tuple[str, str]] = [
    ("sofascore_match_player_stats", "raw/sofascore_match_player_stats__*.parquet"),
    ("sofascore_match_shots", "raw/sofascore_match_shots__*.parquet"),
    ("sofascore_match_team_stats", "raw/sofascore_match_team_stats__*.parquet"),
    ("sofascore_match_momentum", "raw/sofascore_match_momentum__*.parquet"),
    ("sofascore_season", "raw/sofascore__*.parquet"),
    ("understat_rosters", "raw/understat_rosters__*.parquet"),
    ("understat_match_shots", "raw/understat_match_shots__*.parquet"),
    ("understat_match_info", "raw/understat_match_info__*.parquet"),
    ("understat_league_table", "raw/understat_league_table__*.parquet"),
    ("understat_season", "raw/understat__*.parquet"),
    ("clubelo_global", "raw/clubelo__global__*.parquet"),
    ("clubelo_fixtures", "raw/clubelo__fixtures__*.parquet"),
    ("transfermarkt_profiles", "raw/transfermarkt__*.parquet"),
]


def _register_raw_views(con: duckdb.DuckDBPyConnection, data_dir: Path | None) -> None:
    """Register one view per raw parquet family (local paths or R2 URIs)."""
    for view_name, rel_glob in _RAW_FAMILIES:
        _create_view_from_glob(con, view_name, data_dir, rel_glob)


def _register_unified(con: duckdb.DuckDBPyConnection, data_dir: Path | None) -> None:
    be = get_backend()
    if be.exists_rel("unified_player_stats.parquet"):
        path = _parquet_path(data_dir, "unified_player_stats.parquet")
        con.execute(
            f"CREATE OR REPLACE VIEW unified AS SELECT * FROM read_parquet('{path}')"
        )
    elif be.exists_rel("unified_player_stats.csv"):
        if _is_r2():
            # DuckDB httpfs can also read CSV from S3.
            uri = _r2_glob_uri("unified_player_stats.csv")
            con.execute(
                f"CREATE OR REPLACE VIEW unified AS SELECT * FROM read_csv_auto('{uri}')"
            )
        else:
            assert data_dir is not None
            path = _local_glob_path(data_dir, "unified_player_stats.csv")
            con.execute(
                f"CREATE OR REPLACE VIEW unified AS SELECT * FROM read_csv_auto('{path}')"
            )
    else:
        con.execute("CREATE OR REPLACE VIEW unified AS SELECT 1 WHERE false")

    # Search helpers; age_num is derived in cache.py when an ``age`` column exists.
    con.execute(
        """
        CREATE OR REPLACE VIEW unified_prepared AS
        SELECT
            u.*,
            lower(CAST(player AS VARCHAR)) AS _player_lower,
            lower(CAST(team AS VARCHAR)) AS _team_lower,
            COALESCE(
                try_cast(minutes AS DOUBLE),
                try_cast(ninety_s AS DOUBLE) * 90.0
            ) AS minutes_computed
        FROM unified u
        """
    )


def _register_aggregate_views(con: duckdb.DuckDBPyConnection) -> None:
    """Derived views for match-level analytics (no extra parquet writes)."""
    # Per-player per-match log with opponent + team xG context.
    con.execute(
        """
        CREATE OR REPLACE VIEW player_match_log AS
        SELECT
            p.match_id,
            p.player_id,
            p.player_name,
            p.team_name AS team,
            p.is_home,
            CASE
                WHEN COALESCE(p.is_home, false) THEN t.away_team
                ELSE t.home_team
            END AS opponent,
            p.league,
            p.season,
            p.substitute,
            try_cast(p.minutes_played AS DOUBLE) AS minutes_played,
            try_cast(p.rating AS DOUBLE) AS rating,
            try_cast(p.xg AS DOUBLE) AS xg,
            try_cast(p.xgot AS DOUBLE) AS xgot,
            try_cast(p.xa AS DOUBLE) AS xa,
            try_cast(p.goals AS DOUBLE) AS goals,
            try_cast(p.assists AS DOUBLE) AS assists,
            try_cast(p.total_shots AS DOUBLE) AS total_shots,
            try_cast(p.shots_on_target AS DOUBLE) AS shots_on_target,
            try_cast(p.total_passes AS DOUBLE) AS total_passes,
            try_cast(p.accurate_passes AS DOUBLE) AS accurate_passes,
            try_cast(p.touches AS DOUBLE) AS touches,
            try_cast(p.possession_lost AS DOUBLE) AS possession_lost,
            try_cast(p.duels_won AS DOUBLE) AS duels_won,
            try_cast(p.duels_lost AS DOUBLE) AS duels_lost,
            try_cast(p.fouls AS DOUBLE) AS fouls,
            try_cast(p.was_fouled AS DOUBLE) AS was_fouled,
            t.home_team,
            t.away_team,
            try_cast(t.expected_goals_home AS DOUBLE) AS match_xg_home,
            try_cast(t.expected_goals_away AS DOUBLE) AS match_xg_away,
            try_cast(t.ball_possession_home AS DOUBLE) AS possession_home,
            try_cast(t.ball_possession_away AS DOUBLE) AS possession_away
        FROM sofascore_match_player_stats p
        LEFT JOIN sofascore_match_team_stats t
            ON p.match_id = t.match_id
            AND p.league = t.league
            AND p.season = t.season
            AND upper(CAST(t.period AS VARCHAR)) = 'ALL'
        """
    )

    con.execute(
        """
        CREATE OR REPLACE VIEW player_form_profile AS
        SELECT
            player_name,
            league,
            season,
            team,
            count(*) AS matches_played,
            round(avg(rating), 3) AS avg_rating,
            round(stddev_samp(rating), 3) AS rating_std_dev,
            round(avg(CASE WHEN is_home THEN rating END), 3) AS home_avg_rating,
            round(avg(CASE WHEN NOT COALESCE(is_home, false) THEN rating END), 3)
                AS away_avg_rating,
            sum(CASE WHEN rating >= 7.0 THEN 1 ELSE 0 END) AS matches_rated_above_7,
            round(
                sum(xg) / nullif(sum(minutes_played) / 90.0, 0),
                3
            ) AS xg_per90_match_level,
            round(sum(goals), 0) AS total_goals,
            round(sum(assists), 0) AS total_assists,
            round(avg(xg), 3) AS avg_match_xg
        FROM player_match_log
        WHERE rating IS NOT NULL AND rating > 0
        GROUP BY player_name, league, season, team
        """
    )

    con.execute(
        """
        CREATE OR REPLACE VIEW team_season_stats AS
        WITH per_match AS (
            SELECT
                match_id,
                league,
                season,
                home_team AS team,
                true AS is_home,
                try_cast(expected_goals_home AS DOUBLE) AS xg_for,
                try_cast(expected_goals_away AS DOUBLE) AS xg_against,
                try_cast(ball_possession_home AS DOUBLE) AS possession,
                try_cast(total_shots_home AS DOUBLE) AS shots,
                try_cast(big_chances_home AS DOUBLE) AS big_chances_for,
                try_cast(big_chances_away AS DOUBLE) AS big_chances_against
            FROM sofascore_match_team_stats
            WHERE upper(CAST(period AS VARCHAR)) = 'ALL'
            UNION ALL
            SELECT
                match_id,
                league,
                season,
                away_team AS team,
                false AS is_home,
                try_cast(expected_goals_away AS DOUBLE),
                try_cast(expected_goals_home AS DOUBLE),
                try_cast(ball_possession_away AS DOUBLE),
                try_cast(total_shots_away AS DOUBLE),
                try_cast(big_chances_away AS DOUBLE),
                try_cast(big_chances_home AS DOUBLE)
            FROM sofascore_match_team_stats
            WHERE upper(CAST(period AS VARCHAR)) = 'ALL'
        )
        SELECT
            team,
            league,
            season,
            count(*) AS matches,
            round(avg(xg_for), 3) AS avg_xg_for,
            round(avg(xg_against), 3) AS avg_xg_against,
            round(avg(possession), 2) AS avg_possession,
            round(avg(shots), 2) AS avg_shots,
            round(avg(big_chances_for), 2) AS avg_big_chances_for,
            round(avg(big_chances_against), 2) AS avg_big_chances_against,
            round(avg(CASE WHEN is_home THEN xg_for END), 3) AS home_avg_xg_for,
            round(avg(CASE WHEN NOT is_home THEN xg_for END), 3) AS away_avg_xg_for
        FROM per_match
        WHERE team IS NOT NULL AND team <> ''
        GROUP BY team, league, season
        """
    )

    con.execute(
        """
        CREATE OR REPLACE VIEW player_shot_profile AS
        SELECT
            player_name,
            league,
            season,
            count(*) AS shots,
            round(sum(try_cast(xg AS DOUBLE)), 3) AS total_xg,
            round(avg(try_cast(xg AS DOUBLE)), 3) AS avg_xg_per_shot,
            sum(CASE WHEN lower(CAST(body_part AS VARCHAR)) LIKE '%left%' THEN 1 ELSE 0 END)
                AS shots_left_foot,
            sum(CASE WHEN lower(CAST(body_part AS VARCHAR)) LIKE '%right%' THEN 1 ELSE 0 END)
                AS shots_right_foot,
            sum(CASE WHEN lower(CAST(body_part AS VARCHAR)) LIKE '%head%' THEN 1 ELSE 0 END)
                AS shots_headed,
            sum(
                CASE WHEN lower(CAST(situation AS VARCHAR)) IN (
                    'corner', 'free-kick', 'set-piece', 'penalty'
                ) THEN 1 ELSE 0 END
            ) AS shots_set_piece,
            sum(
                CASE WHEN lower(CAST(situation AS VARCHAR)) NOT IN (
                    'corner', 'free-kick', 'set-piece', 'penalty'
                ) OR situation IS NULL THEN 1 ELSE 0 END
            ) AS shots_open_play,
            round(avg(try_cast(player_x AS DOUBLE)), 2) AS avg_shot_x,
            round(avg(try_cast(player_y AS DOUBLE)), 2) AS avg_shot_y
        FROM sofascore_match_shots
        WHERE player_name IS NOT NULL AND player_name <> ''
        GROUP BY player_name, league, season
        """
    )

    # Match index for search (one row per match from team stats ALL period).
    con.execute(
        """
        CREATE OR REPLACE VIEW match_index AS
        SELECT DISTINCT
            match_id,
            home_team,
            away_team,
            league,
            season,
            try_cast(expected_goals_home AS DOUBLE) AS xg_home,
            try_cast(expected_goals_away AS DOUBLE) AS xg_away,
            try_cast(total_shots_home AS DOUBLE) AS shots_home,
            try_cast(total_shots_away AS DOUBLE) AS shots_away,
            try_cast(ball_possession_home AS DOUBLE) AS possession_home,
            try_cast(ball_possession_away AS DOUBLE) AS possession_away
        FROM sofascore_match_team_stats
        WHERE upper(CAST(period AS VARCHAR)) = 'ALL'
        """
    )


def init_db(*, force: bool = False) -> duckdb.DuckDBPyConnection:
    """
    Create in-memory DuckDB, register parquet views and aggregates.

    Automatically configures httpfs for R2 when ``DATA_BACKEND=r2``.
    For the local backend, views use absolute filesystem glob paths instead.
    """
    global _conn, _initialized
    with _lock:
        if _conn is not None and _initialized and not force:
            return _conn
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
        _conn = duckdb.connect(database=":memory:")

        if _is_r2():
            _configure_r2_httpfs(_conn)
            data_dir = None
            log.info("DuckDB: registering views from R2 bucket %s", _r2_bucket())
        else:
            data_dir = _local_data_dir()
            log.info("DuckDB: registering views from local path %s", data_dir)

        _register_raw_views(_conn, data_dir)
        _register_unified(_conn, data_dir)
        _register_aggregate_views(_conn)
        _initialized = True
        return _conn


def refresh() -> None:
    """Re-register all views after a data collection run."""
    init_db(force=True)


def get_connection() -> duckdb.DuckDBPyConnection:
    """Return the shared DuckDB connection (initializes on first use)."""
    if _conn is None or not _initialized:
        return init_db()
    return _conn


def query(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> pd.DataFrame:
    """Run SQL and return a pandas DataFrame."""
    con = get_connection()
    if params:
        return con.execute(sql, params).df()
    return con.execute(sql).df()


def query_scalar(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> Any:
    """Return the first column of the first row, or None."""
    df = query(sql, params)
    if df.empty:
        return None
    return df.iloc[0, 0]


def table_empty(view_name: str) -> bool:
    """True if a registered view has zero rows."""
    n = query_scalar(f"SELECT count(*) FROM {view_name}")
    return n is None or int(n) == 0
