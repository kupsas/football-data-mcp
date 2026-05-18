"""Cloudflare R2 (S3-compatible) storage backend."""

from __future__ import annotations

import fnmatch
import io
import json
import logging
import os

import pandas as pd
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)


class R2Backend:
    """
    R2 object keys mirror local layout: ``raw/<name>.parquet``, ``unified_player_stats.parquet``, etc.
    """

    def __init__(
        self,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
    ) -> None:
        import boto3

        self._bucket = bucket or os.environ["R2_BUCKET"]
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or os.environ["R2_ENDPOINT_URL"],
            aws_access_key_id=access_key or os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=secret_key or os.environ["R2_SECRET_ACCESS_KEY"],
        )

    def read_parquet_rel(self, rel_path: str) -> pd.DataFrame:
        key = rel_path.lstrip("/")
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return pd.read_parquet(io.BytesIO(obj["Body"].read()))

    def read_csv_rel(self, rel_path: str, **kwargs) -> pd.DataFrame:
        key = rel_path.lstrip("/")
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return pd.read_csv(io.BytesIO(obj["Body"].read()), **kwargs)

    def write_parquet_rel(self, rel_path: str, df: pd.DataFrame) -> str:
        key = rel_path.lstrip("/")
        buf = io.BytesIO()
        df.to_parquet(buf, index=False)
        buf.seek(0)
        self._client.put_object(Bucket=self._bucket, Key=key, Body=buf.getvalue())
        uri = f"r2://{self._bucket}/{key}"
        log.info("  Uploaded %s rows → %s", len(df), uri)
        return uri

    def write_csv_rel(self, rel_path: str, df: pd.DataFrame) -> str:
        key = rel_path.lstrip("/")
        buf = io.StringIO()
        df.to_csv(buf, index=False)
        body = buf.getvalue().encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="text/csv; charset=utf-8",
        )
        return f"r2://{self._bucket}/{key}"

    def write_json_rel(self, rel_path: str, data: dict) -> None:
        key = rel_path.lstrip("/")
        body = json.dumps(data, indent=2).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )

    def read_json_rel(self, rel_path: str) -> dict:
        key = rel_path.lstrip("/")
        obj = self._client.get_object(Bucket=self._bucket, Key=key)
        return json.loads(obj["Body"].read().decode("utf-8"))

    def exists_rel(self, rel_path: str) -> bool:
        key = rel_path.lstrip("/")
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def list_raw_glob(self, pattern: str) -> list[str]:
        prefix = "raw/"
        paginator = self._client.get_paginator("list_objects_v2")
        names: list[str] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.startswith(prefix) or not key.endswith(".parquet"):
                    continue
                basename = key[len(prefix) :]
                if fnmatch.fnmatch(basename, pattern):
                    names.append(basename)
        return sorted(names)
