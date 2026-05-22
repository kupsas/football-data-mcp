#!/usr/bin/env python3
"""Re-build EA FC parquets from local staging zips (adds club_name columns)."""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import pandas as pd

from collect_data.collectors.eafc import _duckdb_slim_csv, _normalize_eafc_frame
from collect_data.config import EAFC_SOURCES
from collect_data.storage import RAW_DIR, save_raw

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

STAGING = RAW_DIR / "_eafc_staging"

ZIP_MAP = {
    "fifa23": STAGING / "male_players_fifa23.csv.zip",
    "eafc24": STAGING / "male_players_fc24.csv.zip",
}


def _extract_csv(zip_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        zf.extract("male_players.csv", dest.parent)
    extracted = dest.parent / "male_players.csv"
    if extracted != dest and extracted.exists():
        extracted.rename(dest)


def rebuild_from_zip(source: dict) -> None:
    zip_path = ZIP_MAP.get(source["id"])
    if zip_path is None or not zip_path.exists():
        log.warning("Skip %s — no staging zip at %s", source["id"], zip_path)
        return

    parquet_name = source["parquet_name"]
    csv_path = STAGING / f"{source['id']}_male_players.csv"
    slim_tmp = STAGING / f"{parquet_name}_slim_tmp.parquet"

    log.info("Extracting %s …", zip_path.name)
    _extract_csv(zip_path, csv_path)

    log.info("Slimming %s → parquet …", source["id"])
    _duckdb_slim_csv(
        csv_path,
        slim_tmp,
        fifa_version_filter=source.get("fifa_version_filter"),
    )
    df = pd.read_parquet(slim_tmp)
    slim_tmp.unlink(missing_ok=True)
    csv_path.unlink(missing_ok=True)

    out = _normalize_eafc_frame(
        df,
        season=source["season"],
        schema=source["schema"],
    )
    save_raw(out, parquet_name)
    has_club = out["club_name"].astype(str).str.len().gt(0).sum()
    log.info("✅ %s: %s rows, %s with club_name", parquet_name, len(out), has_club)


def main() -> None:
    STAGING.mkdir(parents=True, exist_ok=True)
    for source in EAFC_SOURCES:
        if source["id"] in ZIP_MAP:
            rebuild_from_zip(source)


if __name__ == "__main__":
    main()
