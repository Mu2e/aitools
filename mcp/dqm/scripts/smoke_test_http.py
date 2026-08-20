#!/usr/bin/env python3
"""Smoke test for a running dqm-mcp: MCP handshake + tool calls over
streamable-HTTP.

Run with the same Python that has `mcp` installed (the release venv), e.g.:
  <deploy-root>/current/.venv/bin/python scripts/smoke_test_http.py http://host:8001

Usage:
  smoke_test_http.py [base-url]   (default: http://127.0.0.1:8001)
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "get_server_info",
    "list_sources",
    "list_versions",
    "list_values",
    "list_intervals",
    "query_metrics",
}


async def check_mcp(base_url: str) -> None:
    url = base_url.rstrip("/") + "/mcp"
    print(f"Connecting to {url} ...")
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"OK: tools/list -> {names}")
            missing = EXPECTED_TOOLS - set(names)
            assert not missing, f"missing expected tools: {sorted(missing)}"

            result = await session.call_tool("get_server_info", {})
            text = result.content[0].text if result.content else ""
            print(f"OK: get_server_info -> {text}")
            info = json.loads(text)
            assert info.get("transport") == "streamable-http", (
                f"unexpected transport in get_server_info: {info.get('transport')!r}"
            )

            result = await session.call_tool("list_sources", {"limit": 1})
            text = result.content[0].text if result.content else ""
            print(f"OK: list_sources(limit=1) -> {text}")


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"
    asyncio.run(check_mcp(base_url))
    print("All checks passed.")


if __name__ == "__main__":
    main()
