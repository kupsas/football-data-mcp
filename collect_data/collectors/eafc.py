"""
EA FC / SoFIFA player attributes from Kaggle → slim ZSTD parquets.

Downloads large CSVs to a staging dir, stream-filters with DuckDB (e.g. FIFA 23-only
from a 5.6 GB multi-version file), normalizes columns, deletes the CSV, saves
``data/raw/eafc__<season>.parquet``.

Requires Kaggle API credentials (``~/.kaggle/kaggle.json`` or env vars) via the
``kaggle`` Python package or ``kaggle`` CLI. Pre-place CSVs under
``data/raw/_eafc_staging/`` to skip download.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

import duckdb
import pandas as pd

from collect_data.config import (
    EAFC_COL_RENAME_ANISS7_2025,
    EAFC_COL_RENAME_ROVNEZ,
    EAFC_COL_RENAME_STEFANO,
    EAFC_OUTPUT_COLUMNS,
    EAFC_SOURCES,
)
from collect_data.helpers import _norm_name, _norm_team
from collect_data.storage import RAW_DIR, save_raw

log = logging.getLogger(__name__)

STAGING_DIR = RAW_DIR / "_eafc_staging"


def _ensure_staging() -> Path:
    STAGING_DIR.mkdir(parents=True, exist_ok=True)
    return STAGING_DIR


def _download_via_kaggle_cli(owner_slug: str, dataset_slug: str, file_name: str, dest: Path) -> bool:
    """Download with ``kaggle datasets download`` if the CLI is on PATH."""
    kaggle_bin = shutil.which("kaggle")
    if not kaggle_bin:
        return False

    dest.parent.mkdir(parents=True, exist_ok=True)
    slug = f"{owner_slug}/{dataset_slug}"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cmd = [
            kaggle_bin,
            "datasets",
            "download",
            "-d",
            slug,
            "-f",
            file_name,
            "-p",
            str(tmp_path),
            "--quiet",
        ]
        log.info("  Running: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, capture_output=True, text=True)

        downloaded = tmp_path / file_name
        zip_path = tmp_path / f"{file_name}.zip"
        if downloaded.exists():
            shutil.move(str(downloaded), dest)
            return True
        if zip_path.exists():
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extract(file_name, tmp_path)
            shutil.move(str(tmp_path / file_name), dest)
            zip_path.unlink(missing_ok=True)
            return True
    return False


def _download_kaggle_file(owner_slug: str, dataset_slug: str, file_name: str, dest: Path) -> None:
    """Download a Kaggle dataset file; raise if all methods fail."""
    if dest.exists() and dest.stat().st_size > 0:
        log.info("  Using existing staging file: %s", dest.name)
        return

    staging_name = f"{owner_slug}__{dataset_slug}__{file_name}".replace("/", "_")
    alt = STAGING_DIR / staging_name
    if alt.exists() and alt.stat().st_size > 0:
        shutil.copy2(alt, dest)
        log.info("  Copied staging file %s", alt.name)
        return

    if os.getenv("EAFC_SKIP_DOWNLOAD", "").lower() in ("1", "true", "yes"):
        raise FileNotFoundError(
            f"EAFC_SKIP_DOWNLOAD set but {dest} missing. Place CSV at {dest} or {alt}"
        )

    log.info("  Downloading Kaggle %s/%s (%s) …", owner_slug, dataset_slug, file_name)

    # Kaggle Python API (preferred on VPS).
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            api = KaggleApi()
            api.authenticate()
            api.dataset_download_file(
                f"{owner_slug}/{dataset_slug}",
                file_name,
                path=str(tmp_path),
                quiet=False,
            )
            extracted = tmp_path / file_name
            zip_path = tmp_path / f"{file_name}.zip"
            if extracted.exists():
                shutil.move(str(extracted), dest)
                return
            if zip_path.exists():
                with zipfile.ZipFile(zip_path, "r") as zf:
                    zf.extract(file_name, tmp_path)
                shutil.move(str(tmp_path / file_name), dest)
                return
    except ImportError:
        log.debug("  kaggle package not installed")
    except Exception as e:
        log.warning("  Kaggle API download failed: %s", e)

    if _download_via_kaggle_cli(owner_slug, dataset_slug, file_name, dest):
        return

    raise RuntimeError(
        f"Could not download {owner_slug}/{dataset_slug}/{file_name}. "
        "Install `pip install kaggle`, configure ~/.kaggle/kaggle.json, or place the CSV in "
        f"{STAGING_DIR}"
    )


def _duckdb_slim_csv(
    csv_path: Path,
    out_parquet: Path,
    *,
    fifa_version_filter: int | None,
) -> None:
    """
    Stream-read CSV with DuckDB, optional version filter, write ZSTD parquet.
    Does not rename columns yet — that happens in pandas.
    """
    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    path_sql = str(csv_path.resolve()).replace("'", "''")
    if fifa_version_filter is not None:
        ver = int(fifa_version_filter)
        sql = f"""
        COPY (
            SELECT * FROM read_csv_auto('{path_sql}')
            WHERE CAST(fifa_version AS INTEGER) = {ver}
        ) TO '{str(out_parquet.resolve()).replace("'", "''")}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    else:
        sql = f"""
        COPY (
            SELECT * FROM read_csv_auto('{path_sql}')
        ) TO '{str(out_parquet.resolve()).replace("'", "''")}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    log.info("  DuckDB slim: %s → %s", csv_path.name, out_parquet.name)
    con.execute(sql)
    pq = str(out_parquet.resolve()).replace("'", "''")
    row_count = con.execute(f"SELECT count(*) FROM read_parquet('{pq}')").fetchone()[0]
    con.close()
    log.info("  Slim parquet rows: %s (%.2f MB)", row_count, out_parquet.stat().st_size / 1e6)


def _parse_work_rate(raw: str | float | None) -> tuple[str | None, str | None]:
    """Split SoFIFA ``Medium/High`` into attacking and defending work rates."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None, None
    s = str(raw).strip()
    if "/" not in s:
        return s, None
    parts = s.split("/", 1)
    return parts[0].strip(), parts[1].strip()


def _age_from_dob(dob: object, *, as_of_year: int = 2025) -> float | None:
    """Approximate age from SoFIFA ``dob`` string for the June 2025 snapshot."""
    if dob is None or (isinstance(dob, float) and pd.isna(dob)):
        return None
    ts = pd.to_datetime(dob, errors="coerce")
    if pd.isna(ts):
        return None
    return float(as_of_year - ts.year)


def _normalize_eafc_frame(df: pd.DataFrame, *, season: str, schema: str) -> pd.DataFrame:
    """Rename columns, parse traits/work rate, add season and _name_norm."""
    if schema == "aniss7_2025":
        rename = EAFC_COL_RENAME_ANISS7_2025
    elif schema == "rovnez":
        rename = EAFC_COL_RENAME_ROVNEZ
    else:
        rename = EAFC_COL_RENAME_STEFANO
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    df = df.loc[:, ~df.columns.duplicated(keep="first")]

    if "player" not in df.columns and "player_long" in df.columns:
        df["player"] = df["player_long"]
    if "player" not in df.columns:
        raise ValueError("EA FC frame missing player name column")

    df["player"] = (
        df["player"]
        .astype(str)
        .str.strip("'\"")
        .str.replace(r"\s*-\s*$", "", regex=True)
        .str.strip()
    )
    df["_name_norm"] = df["player"].apply(_norm_name)
    if "club_name" in df.columns:
        df["club_name"] = df["club_name"].astype(str).replace("nan", "")
    if "club_league_name" in df.columns:
        df["club_league_name"] = df["club_league_name"].astype(str).replace("nan", "")
    if "club_name" in df.columns:
        df["_club_norm"] = df["club_name"].apply(_norm_team)
    else:
        df["club_name"] = ""
        df["_club_norm"] = ""
    df["season"] = season

    wr_col = "work_rate_raw" if "work_rate_raw" in df.columns else None
    if wr_col:
        parsed = df[wr_col].apply(_parse_work_rate)
        df["work_rate_attacking"] = parsed.apply(lambda t: t[0])
        df["work_rate_defending"] = parsed.apply(lambda t: t[1])
        df = df.drop(columns=[wr_col], errors="ignore")

    if "player_traits" in df.columns:
        df["player_traits"] = df["player_traits"].astype(str).replace("nan", "")
    elif "player_traits_alt" in df.columns:
        df["player_traits"] = df["player_traits_alt"].astype(str).replace("nan", "")
        df = df.drop(columns=["player_traits_alt"], errors="ignore")

    if "age" not in df.columns and "dob" in df.columns:
        df["age"] = df["dob"].apply(_age_from_dob)

    for col in EAFC_OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = None

    out = df[[c for c in EAFC_OUTPUT_COLUMNS if c in df.columns]].copy()
    for col in EAFC_OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = None
    out = out[EAFC_OUTPUT_COLUMNS]

    numeric_like = [
        c for c in out.columns
        if c not in ("player", "_name_norm", "_club_norm", "club_name", "club_league_name",
                     "positions", "nationality", "season",
                     "preferred_foot", "work_rate_attacking", "work_rate_defending",
                     "player_traits", "body_type")
    ]
    for col in numeric_like:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    return out


def _process_source(source: dict, *, force: bool = False) -> None:
    """Download, slim, normalize, and save one EA FC season parquet."""
    parquet_name = source["parquet_name"]
    out_path = RAW_DIR / f"{parquet_name}.parquet"
    if out_path.exists() and not force:
        log.info("  ⏭️  %s already exists", out_path.name)
        return

    staging = _ensure_staging()
    csv_dest = staging / source["file_name"]

    _download_kaggle_file(
        source["owner_slug"],
        source["dataset_slug"],
        source["file_name"],
        csv_dest,
    )

    slim_tmp = staging / f"{parquet_name}_slim_tmp.parquet"
    try:
        _duckdb_slim_csv(
            csv_dest,
            slim_tmp,
            fifa_version_filter=source.get("fifa_version_filter"),
        )
        df = pd.read_parquet(slim_tmp)
        df = _normalize_eafc_frame(
            df,
            season=source["season"],
            schema=source["schema"],
        )
        save_raw(df, parquet_name)
    finally:
        slim_tmp.unlink(missing_ok=True)
        if os.getenv("EAFC_DELETE_STAGING_CSV", "1").lower() not in ("0", "false", "no"):
            try:
                csv_dest.unlink()
                log.info("  Deleted staging CSV %s", csv_dest.name)
            except OSError as e:
                log.warning("  Could not delete staging CSV: %s", e)


def collect_eafc(*, force: bool = False) -> None:
    """
    Download and ingest all configured EA FC seasons into ``data/raw/eafc__*.parquet``.
    """
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    log.info("── EA FC attributes (Kaggle → slim parquet) ─────────────────────────")
    for source in EAFC_SOURCES:
        log.info("  Processing %s (%s)", source["id"], source["season"])
        try:
            _process_source(source, force=force)
        except Exception as e:
            log.error("  ❌ EA FC %s failed: %s", source["id"], e)
