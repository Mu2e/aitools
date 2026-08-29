#!/usr/bin/env python3
"""Smoke test for a running runs-mcp: MCP handshake + tool calls over
streamable-HTTP.

Run with the same Python that has `mcp` installed (the release venv), e.g.:
  <deploy-root>/current/.venv/bin/python scripts/smoke_test_http.py \
      http://host:8006 mikey_<token>

Usage:
  smoke_test_http.py [base-url] [bearer-token]
    base-url      default: http://127.0.0.1:8006
    bearer-token  optional -- omit only if this deployment has auth disabled
                  (MIKEY_KEYS_FILE unset); otherwise every call gets a clean
                  401 and this script fails loudly rather than silently.
"""

from __future__ import annotations

import asyncio
import json
import sys

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

EXPECTED_TOOLS = {
    "get_server_info",
    "get_run",
    "list_runs",
    "get_flags",
    "get_dbtables",
    "get_cidtables",
    "get_config_blob",
}


async def check_mcp(base_url: str, token: str | None) -> None:
    url = base_url.rstrip("/") + "/mcp"
    print(f"Connecting to {url} ...")

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(headers=headers) as http_client:
        async with streamable_http_client(url, http_client=http_client) as (read, write):
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

                result = await session.call_tool("list_runs", {"last": 3})
                # A list[...]-annotated tool return serializes as one text
                # content block PER ELEMENT, not a single JSON-array blob --
                # confirmed directly against a live server, not assumed.
                # len(result.content) is therefore the actual row count.
                runs = [json.loads(block.text) for block in result.content]
                print(f"OK: list_runs(last=3) -> {len(runs)} row(s), first: {json.dumps(runs[0]) if runs else None}")
                assert len(runs) <= 3, f"list_runs(last=3) returned more than 3 rows: {len(runs)}"

                result = await session.call_tool("get_flags", {})
                text = result.content[0].text if result.content else ""
                print(f"OK: get_flags -> {text[:200]}")


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8006"
    token = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(check_mcp(base_url, token))
    print("All checks passed.")


if __name__ == "__main__":
    main()
