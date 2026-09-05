#!/usr/bin/env python3
"""Smoke test for a running memory-mcp: MCP handshake + tool calls over
streamable-HTTP.

Run with the same Python that has `mcp` installed (the release venv), e.g.:
  <deploy-root>/current/.venv/bin/python scripts/smoke_test_http.py \
      http://host:8007 mikey_<token>

Usage:
  smoke_test_http.py [base-url] [bearer-token] [--write]
    base-url      default: http://127.0.0.1:8007
    bearer-token  REQUIRED in practice -- memory-mcp always enforces auth, so
                  without a token every call gets a 401.
    --write       also exercise put_document/set_metadata/get_document.

Read-only by default, deliberately. memory-mcp is append-only: it cannot
delete, so anything --write creates is PERMANENT and cannot be cleaned up
afterwards by this script or by the server. --write uses the project name
'_smoketest' so the residue is at least obvious and easy to ignore (or to
retire via set_metadata, or to purge by hand in psql -- see
sql/create_tables.sql).
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
    "list_projects",
    "list_documents",
    "list_versions",
    "get_document",
    "put_document",
    "set_metadata",
}

SMOKE_PROJECT = "_smoketest"


def _blocks(result) -> list:
    """A list[...]-annotated tool serializes as one text content block PER
    ELEMENT, not a single JSON array -- so the block count is the row count."""
    return [json.loads(b.text) for b in result.content if getattr(b, "text", None)]


def _one(result):
    return _blocks(result)[0] if result.content else None


async def check_mcp(base_url: str, token: str | None, do_write: bool) -> None:
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

                info = _one(await session.call_tool("get_server_info", {}))
                print(f"OK: get_server_info -> {json.dumps(info)}")
                assert info.get("transport") == "streamable-http", info
                assert info.get("auth") == "mikey", (
                    f"expected auth=mikey, got {info.get('auth')!r} -- this server "
                    "should never run without auth"
                )
                owner = info.get("owner")
                assert owner, "no owner reported; the token did not resolve to a name"
                print(f"OK: authenticated as owner {owner!r}")

                projects = _blocks(await session.call_tool("list_projects", {}))
                print(f"OK: list_projects -> {len(projects)} project(s): {projects}")

                docs = _blocks(await session.call_tool("list_documents", {"limit": 5}))
                print(f"OK: list_documents(limit=5) -> {len(docs)} row(s)")
                for d in docs:
                    assert "content" not in d, "list_documents must never return content"

                if not do_write:
                    print("(skipping write tests; pass --write to exercise them)")
                    return

                name = "smoke-test-doc"
                put = _one(await session.call_tool("put_document", {
                    "project": SMOKE_PROJECT,
                    "name": name,
                    "content": "# smoke test\n\nfirst line\n",
                    "description": "Written by smoke_test_http.py; safe to retire.",
                    "keywords": "smoketest,disposable",
                    "weight": 1,
                }))
                print(f"OK: put_document -> {json.dumps(put)}")
                first_version = put["version"]

                appended = _one(await session.call_tool("put_document", {
                    "project": SMOKE_PROJECT,
                    "name": name,
                    "content": "second line\n",
                    "mode": "append",
                }))
                print(f"OK: put_document(mode=append) -> version {appended['version']}")
                assert appended["version"] == first_version + 1, (first_version, appended)
                assert appended["size_bytes"] > put["size_bytes"], "append did not grow the document"

                got = _one(await session.call_tool("get_document", {
                    "project": SMOKE_PROJECT, "name": name,
                }))
                assert "second line" in got["content"], got
                assert "first line" in got["content"], "append lost the earlier content"
                print(f"OK: get_document -> version {got['version']}, {got['size_bytes']} bytes")

                old = _one(await session.call_tool("get_document", {
                    "project": SMOKE_PROJECT, "name": name, "version": first_version,
                }))
                assert "second line" not in old["content"], "old version was mutated"
                print(f"OK: get_document(version={first_version}) still returns the original text")

                versions = _blocks(await session.call_tool("list_versions", {
                    "project": SMOKE_PROJECT, "name": name,
                }))
                print(f"OK: list_versions -> {len(versions)} version(s)")
                assert len(versions) >= 2, versions

                meta = _one(await session.call_tool("set_metadata", {
                    "project": SMOKE_PROJECT, "name": name, "weight": 2,
                }))
                assert meta["metadata"]["weight"] == 2, meta
                assert meta["metadata"]["description"], (
                    "carry-forward failed: description was blanked by a partial update"
                )
                print("OK: set_metadata merged (weight updated, description carried forward)")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--write"]
    do_write = "--write" in sys.argv[1:]
    base_url = args[0] if args else "http://127.0.0.1:8007"
    token = args[1] if len(args) > 1 else None
    asyncio.run(check_mcp(base_url, token, do_write))
    print("All checks passed.")


if __name__ == "__main__":
    main()
