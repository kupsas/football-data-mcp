"""Tests for MCP ``data_status`` coverage calculation (Parquet/string dtypes)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd

from soccer_server.tools import _coverage_pct, tool_data_status


class _FakeBackend:
    def list_raw_glob(self, pattern: str) -> list[str]:
        return []

    def exists_rel(self, rel_path: str) -> bool:
        return False


def test_coverage_pct_numeric_and_string_dtypes() -> None:
    goals = pd.Series([1, 2], dtype="int64")
    assert _coverage_pct(goals) == 100  # both > 0

    dates = pd.Series(["2025-06-30", None], dtype="string")
    assert _coverage_pct(dates) == 50  # one non-null

    assert _coverage_pct(pd.Series([], dtype="float64")) == 0


@patch("soccer_server.tools.get_backend")
@patch("soccer_server.tools.get_unified")
def test_tool_data_status_string_column_no_type_error(
    mock_get_unified: MagicMock,
    mock_get_backend: MagicMock,
) -> None:
    """Regression: string dtype (non-object) must not be compared to 0."""
    mock_get_backend.return_value = _FakeBackend()
    mock_get_unified.return_value = pd.DataFrame(
        {
            "season": ["2023-24", "2023-24"],
            "league": ["EPL", "EPL"],
            "goals": [5, 0],
            "contract_expiration": pd.Series(["2026-06-30", None], dtype="string"),
        }
    )

    result = tool_data_status({})

    assert result["status"] == "OK"
    cov = result["coverage"]["2023-24"]
    assert cov["goals"] == "50%"  # one of two rows > 0
    assert cov["contract_expiration"] == "50%"  # one of two non-null
