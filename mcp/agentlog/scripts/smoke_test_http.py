#!/usr/bin/env python3
"""Smoke test for a running agentlog-mcp: MCP handshake + tool calls over
streamable-HTTP.

Run with the same Python that has `mcp` installed (the release venv), e.g.:
  <deploy-root>/current/.venv/bin/python scripts/smoke_test_http.py \
      http://<host>:<port> mikey_<token>

Usage:
  smoke_test_http.py <base-url> [bearer-token] [--write]
    base-url      REQUIRED, e.g. http://127.0.0.1:<port>. There is no default:
                  this package records no port anywhere, deployed or local.
    bearer-token  REQUIRED in practice -- agentlog-mcp always enforces auth, so
                  without a token every call gets a 401.
    --write       also exercise file_report / comment_on_report / get_report.

READ-ONLY BY DEFAULT, and the reason is stronger here than in memory-mcp.
agentlog is append-only with no retirement flag: anything --write files is
PERMANENT, public to every reader of the log, and cannot be removed by this
script, by the server, or by any tool it exposes -- only by a database
administrator running DELETE by hand.

What --write leaves behind, deliberately made obvious:
  * two reports titled '[smoketest] ...', tagged 'smoketest', agent
    'smoke_test_http.py' -- the second one commenting on the first
  * a 'free:agentlog-smoketest' ref, and a 'run:1' ref (run 1 is not a real
    data run; it is here because the run projection columns and the run range
    query are worth proving end to end -- that path had a constraint bug, see
    sql/migrations/001)

So: run it with --write once against a new deployment, then read-only after
that.
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
    "file_report",
    "comment_on_report",
    "list_reports",
    "get_report",
    "list_refs",
}

SMOKE_TAG = "smoketest"
SMOKE_REF = "free:agentlog-smoketest"
SMOKE_RUN = 1


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
                # The log is append-only all the way down; a tool that claims
                # otherwise would be a design regression, not just a bug.
                for forbidden in ("update_report", "delete_report", "edit_report"):
                    assert forbidden not in names, (
                        f"{forbidden} exists -- agentlog is append-only by design"
                    )

                info = _one(await session.call_tool("get_server_info", {}))
                print(f"OK: get_server_info -> {json.dumps(info)}")
                assert info.get("transport") == "streamable-http", info
                assert info.get("auth") == "mikey", (
                    f"expected auth=mikey, got {info.get('auth')!r} -- this server "
                    "should never run without auth"
                )
                assert info.get("append_only") is True, info
                author = info.get("author")
                assert author, "no author reported; the token did not resolve to a name"
                print(f"OK: authenticated as author {author!r}")

                recent = _blocks(await session.call_tool("list_reports", {"limit": 5}))
                print(f"OK: list_reports(limit=5) -> {len(recent)} row(s)")
                for row in recent:
                    assert "body" not in row, "list_reports must never return bodies"
                    assert "body_size" in row, row

                refs = _blocks(await session.call_tool("list_refs", {"limit": 5}))
                print(f"OK: list_refs(limit=5) -> {len(refs)} row(s)")

                # A filter that should match nothing, to prove filtering is
                # actually applied rather than ignored.
                nothing = _blocks(await session.call_tool("list_reports", {
                    "ref": "free:no-such-thing-exists-here",
                }))
                assert nothing == [], f"expected no rows, got {len(nothing)}"
                print("OK: list_reports with an unmatched ref returns nothing")

                if not do_write:
                    print("(skipping write tests; pass --write to exercise them)")
                    return

                print("--- write tests: everything below is PERMANENT ---")
                filed = _one(await session.call_tool("file_report", {
                    "title": "[smoketest] agentlog-mcp write path check",
                    "body": (
                        "## What this is\n\nWritten by scripts/smoke_test_http.py to "
                        "verify the write path end to end. It is not a real "
                        "investigation and can be ignored.\n\n"
                        "## Why it cannot be deleted\n\nagentlog is append-only: the "
                        "MCP's database role has INSERT and SELECT and nothing else.\n"
                    ),
                    "summary": "Disposable report written by the smoke test.",
                    "severity": "info",
                    "triggered_by": "agent",
                    "agent": "smoke_test_http.py",
                    "subjects": f"{SMOKE_REF}\nrun:{SMOKE_RUN}",
                    "tags": SMOKE_TAG,
                    "attrs": '{"smoketest": true}',
                }))
                print(f"OK: file_report -> {json.dumps(filed)}")
                report_id = filed["report_id"]
                assert filed["author"] == author, filed
                assert not filed["warnings"], filed["warnings"]
                kinds = {(r["ref_type"], r["ref_key"]) for r in filed["refs"]}
                assert ("run", str(SMOKE_RUN)) in kinds, filed["refs"]
                assert ("free", "agentlog-smoketest") in kinds, filed["refs"]

                got = _one(await session.call_tool("get_report", {"report_id": report_id}))
                assert "append-only" in got["body"], got
                assert got["attrs"] == {"smoketest": True}, got
                assert got["tags"] == [SMOKE_TAG], got
                assert len(got["refs"]) == 2, got["refs"]
                print(f"OK: get_report -> {got['body_size']} byte body, "
                      f"{len(got['refs'])} refs")

                # The run projection and the range query: the reason run refs
                # carry run_lo/run_hi at all.
                by_run = _blocks(await session.call_tool("list_reports", {
                    "run": SMOKE_RUN, "limit": 10,
                }))
                assert any(r["report_id"] == report_id for r in by_run), (
                    f"report {report_id} not found by list_reports(run={SMOKE_RUN}) -- "
                    "the run projection columns are not working"
                )
                print(f"OK: list_reports(run={SMOKE_RUN}) found the report")

                by_text = _blocks(await session.call_tool("list_reports", {
                    "text": "smoke test write path", "limit": 10,
                }))
                assert any(r["report_id"] == report_id for r in by_text), (
                    "full-text search did not find the report -- is search_vec populated?"
                )
                assert all("rank" in r for r in by_text), by_text[:1]
                print("OK: full-text search found the report, ranked")

                commented = _one(await session.call_tool("comment_on_report", {
                    "report_id": report_id,
                    "body": "Correcting the smoke test report, to exercise the trail.",
                    "relation": "supersedes",
                    "triggered_by": "agent",
                    "agent": "smoke_test_http.py",
                    "tags": SMOKE_TAG,
                }))
                print(f"OK: comment_on_report -> {json.dumps(commented['link'])}")
                comment_id = commented["report_id"]
                assert commented["link"]["to_report"] == report_id, commented
                # inherit_subjects defaults to true, so the correction must be
                # findable the same ways the original is.
                assert commented["inherited_subjects"] >= 2, commented
                inherited = _blocks(await session.call_tool("list_reports", {
                    "run": SMOKE_RUN, "limit": 10,
                }))
                assert any(r["report_id"] == comment_id for r in inherited), (
                    "the correction did not inherit the run subject"
                )
                print("OK: the correction inherited the original's subjects")

                # Nothing is hidden: the superseded report is still listed, and
                # now carries the id of what corrected it.
                trail = _one(await session.call_tool("get_report", {
                    "report_id": report_id, "include_body": False,
                }))
                assert trail.get("body") is None, "include_body=false still returned a body"
                roles = {(l["role"], l["report_id"]) for l in trail["incoming_links"]}
                assert ("supersedes", comment_id) in roles, trail["incoming_links"]
                print("OK: the original still reads back, with the correction linked to it")

                listed = _blocks(await session.call_tool("list_reports", {
                    "tags": SMOKE_TAG, "limit": 10,
                }))
                original = next(r for r in listed if r["report_id"] == report_id)
                assert comment_id in original["corrected_by"], original
                print("OK: list_reports still shows the corrected report, flagged "
                      f"corrected_by={original['corrected_by']}")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--write"]
    do_write = "--write" in sys.argv[1:]
    if not args:
        print(__doc__, file=sys.stderr)
        sys.exit(2)
    base_url = args[0]
    token = args[1] if len(args) > 1 else None
    asyncio.run(check_mcp(base_url, token, do_write))
    print("All checks passed.")


if __name__ == "__main__":
    main()
