"""stdio MCP transport: line-delimited JSON-RPC on stdin/stdout."""

from __future__ import annotations

import json
import logging
import os
import sys

from soccer_server import db
from soccer_server.cache import get_unified
from soccer_server.registry import TOOLS

log = logging.getLogger(__name__)

# Honour MCP / CLI logging expectations unless user configured logging already.
if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s  %(message)s",
    )


def _stdio_include_hints() -> bool:
    """If true (default), tool error payloads append a short ``_stdio_note`` for local users."""
    return os.getenv("MCP_STDIO_TOOL_HINTS", "1").strip().lower() not in ("0", "false", "no")


def _format_tool_result(result: dict) -> str:
    """
    Serialize tool result for MCP ``content[].text``.

    For dicts with ``hint`` (structured tool errors), optionally add ``_stdio_note`` so
    Claude Desktop users see the local fix-it line without changing the canonical ``error`` field.
    """
    if _stdio_include_hints() and isinstance(result, dict) and result.get("hint"):
        out = {**result, "_stdio_note": f"{result.get('error', '')} ({result['hint']})"}
        return json.dumps(out, indent=2, default=str)
    return json.dumps(result, indent=2, default=str)


def _respond(req_id, result):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result}) + "\n")
    sys.stdout.flush()


def _error(req_id, code: int, message: str):
    sys.stdout.write(
        json.dumps({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})
        + "\n"
    )
    sys.stdout.flush()


def handle_request(req: dict):
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        _respond(
            req_id,
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "soccer-data", "version": "3.2"},
            },
        )

    elif method == "tools/list":
        _respond(
            req_id,
            {
                "tools": [
                    {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
                    for name, meta in TOOLS.items()
                ]
            },
        )

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name not in TOOLS:
            _error(req_id, -32601, f"Unknown tool: {tool_name}")
            return

        try:
            result = TOOLS[tool_name]["fn"](tool_args)
            _respond(
                req_id,
                {"content": [{"type": "text", "text": _format_tool_result(result)}]},
            )
        except Exception as e:
            log.exception("Error running tool %s", tool_name)
            _error(req_id, -32603, str(e))

    elif method == "notifications/initialized":
        pass

    else:
        if req_id is not None:
            _error(req_id, -32601, f"Method not found: {method}")


def main():
    db.init_db()  # Register parquet views and aggregate analytics views.
    get_unified()  # Warm unified table cache once at startup.
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handle_request(req)
        except json.JSONDecodeError as e:
            log.error("JSON decode error: %s", e)


if __name__ == "__main__":
    main()
