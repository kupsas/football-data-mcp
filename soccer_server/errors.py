"""Structured tool errors: machine-readable codes + neutral messages + optional local hints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolError:
    """Structured error returned from a tool implementation."""

    code: str
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"error_code": self.code, "error": self.message}
        if self.hint is not None:
            out["hint"] = self.hint
        return out


def no_data_error() -> dict[str, Any]:
    return ToolError(
        code="NO_DATA",
        message="No player data is currently available.",
        hint="Run: python -m collect_data",
    ).to_dict()


def not_found_error(entity: str, query: str) -> dict[str, Any]:
    return ToolError(
        code="NOT_FOUND",
        message=f"No {entity} found matching {query!r}.",
    ).to_dict()


def not_found_with_suggestions(
    entity: str,
    query: str,
    suggestions: list[str],
) -> dict[str, Any]:
    """NOT_FOUND plus optional ``did_you_mean`` name list for fuzzy player search."""
    out = not_found_error(entity, query)
    if suggestions:
        out["did_you_mean"] = suggestions
    return out


def missing_param_error(param: str) -> dict[str, Any]:
    return ToolError(
        code="INVALID_PARAM",
        message=f"Required parameter missing or empty: {param}.",
    ).to_dict()


def invalid_param_value_error(message: str) -> dict[str, Any]:
    return ToolError(code="INVALID_PARAM", message=message).to_dict()


def missing_source_error(source: str, collect_hint: str) -> dict[str, Any]:
    return ToolError(
        code="MISSING_SOURCE",
        message=f"{source} is not available in the current dataset.",
        hint=collect_hint,
    ).to_dict()


def generic_error(message: str, *, hint: str | None = None) -> dict[str, Any]:
    return ToolError(code="ERROR", message=message, hint=hint).to_dict()
