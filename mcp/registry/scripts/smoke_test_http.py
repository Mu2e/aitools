#!/usr/bin/env python3
"""Smoke test for a running registry-mcp: MCP handshake + tool calls, plus
the plain GET /registry and GET /list endpoints.

Run with the same Python that has `mcp` installed (the release venv), e.g.:
  <deploy-root>/current/.venv/bin/python scripts/smoke_test_http.py http://host:8000

Usage:
  smoke_test_http.py [base-url]   (default: http://127.0.0.1:8000)
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def check_registry(base_url: str) -> None:
    url = base_url.rstrip("/") + "/registry"
    with urllib.request.urlopen(url, timeout=5) as resp:
        data = json.load(resp)
    assert "mcpServers" in data, f"malformed /registry response: {data!r}"
    for name, info in data["mcpServers"].items():
        assert "description" in info, f"missing description for {name!r}: {info!r}"
    print(f"OK: GET /registry -> {json.dumps(data)}")


def check_list_page(base_url: str) -> None:
    url = base_url.rstrip("/") + "/list"
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = resp.read().decode("utf-8")
        content_type = resp.headers.get("Content-Type", "")
    assert "text/html" in content_type, f"unexpected Content-Type: {content_type!r}"
    assert "<table>" in body, "malformed /list response: no <table> found"
    print(f"OK: GET /list -> {len(body)} bytes of HTML")


async def check_mcp(base_url: str) -> None:
    url = base_url.rstrip("/") + "/mcp"
    print(f"Connecting to {url} ...")
    async with streamable_http_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"OK: tools/list -> {names}")
            assert "say_hello" in names, "say_hello tool missing"
            assert "list_mcp_servers" in names, "list_mcp_servers tool missing"

            result = await session.call_tool("say_hello", {"name": "smoke-test"})
            text = result.content[0].text if result.content else ""
            print(f"OK: say_hello -> {text!r}")
            assert "smoke-test" in text

            result = await session.call_tool("list_mcp_servers", {})
            text = result.content[0].text if result.content else ""
            print(f"OK: list_mcp_servers -> {text}")


def main() -> None:
    base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    check_registry(base_url)
    check_list_page(base_url)
    asyncio.run(check_mcp(base_url))
    print("All checks passed.")


if __name__ == "__main__":
    main()
