"""Usage log service: takes the harnesses' usage records and inserts them.

Users have no role in the database that holds usage.sessions (../sessions.sql),
so their harnesses (Claude Code, goose, hep-genesis) cannot write there
themselves. This service runs as the account that can (mu2eai) and inserts
for them:

    POST /usage     one record, or a list of records, in the shape of
                    ../usage_record.py (the columns of usage.sessions), with
                    "Authorization: Bearer <mikey key>"
    GET  /summary   the usage of the last ?days=30 (1 to 366) as JSON, from
                    usage.sessions_current: per interface and model, and per
                    day. The same key as for posting.
    GET  /health    {"ok": true, "version": ...}; no key needed

A record is checked for shape and size and then inserted as it is. Nothing
about the sender is stored or logged: no key name, no address. Rows are
insert-only (ON CONFLICT DO NOTHING on session_id + logged_at), so a record
sent twice is one row.

The answer to a POST is 200 with {"received", "inserted", "rejected"}:
"rejected" lists the records that were not taken, with the reason, and the
others are inserted all the same -- one bad record never holds up a backlog.
401: no valid key. 400: not JSON, or not a record or a list. 413: too large.
503: the database could not be reached; the sender keeps the records.

Environment:
    MIKEY_KEYS_FILE   the hashed key file (required; mikey)
    LOG_PG_DSN        e.g. postgresql://ifdb11:5477/mu2e_ai_prd (required)
    KRB5CCNAME        the Kerberos cache that is the database credential
    LOG_PG_TABLE      default usage.sessions
    USAGE_INTERFACES  accepted "interface" values, comma-separated
                      (default: slack,goose,claude-code,hep-genesis)

    usage-server --check     try the key file and the database, and exit
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any, Callable

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route

log = logging.getLogger("usage-server")

# The columns of usage.sessions, in table order. Keep in sync with
# ../sessions.sql and ../usage_record.py (a test compares them).
TEXT = ("session_id", "interface", "harness_version", "host", "provider", "endpoint_url", "model")
TIMES = ("logged_at", "session_created_at", "session_updated_at")
COUNTS = ("turns", "llm_calls", "tool_calls",
          "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "thinking_tokens")
COLUMNS = (
    "session_id", "interface", "harness_version", "host", "logged_at",
    "session_created_at", "session_updated_at", "provider", "endpoint_url", "model",
    "turns", "llm_calls", "tool_calls", "tool_breakdown",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "thinking_tokens",
)
REQUIRED = ("session_id", "interface", "logged_at")

MAX_BODY = 1_000_000          # bytes
MAX_RECORDS = 500             # per request (a backlog)
MAX_TEXT = 512
MAX_TOOLS = 1000              # entries of tool_breakdown
MAX_COUNT = 2**62
DEFAULT_INTERFACES = "slack,goose,claude-code,hep-genesis"
DEFAULT_PORT = 8199


def service_version() -> str:
    try:
        return _pkg_version("mu2e-usage-server")
    except PackageNotFoundError:
        return "unknown"


def interfaces() -> set[str]:
    return {i.strip() for i in os.environ.get("USAGE_INTERFACES", DEFAULT_INTERFACES).split(",") if i.strip()}


# ---------------------------------------------------------------- validation


def _time(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name}: not a timestamp")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"{name}: not an ISO-8601 timestamp") from None
    if dt.tzinfo is None:
        raise ValueError(f"{name}: no UTC offset")
    if dt > datetime.now(timezone.utc) + timedelta(hours=1):
        raise ValueError(f"{name}: in the future")
    return dt.isoformat()


def clean(record: Any, allowed: set[str]) -> dict:
    """The record with every column (missing ones None), or ValueError saying
    what is wrong with it. Nothing is added and nothing is corrected."""
    if not isinstance(record, dict):
        raise ValueError("not an object")
    unknown = sorted(set(record) - set(COLUMNS))
    if unknown:
        raise ValueError(f"not a column: {', '.join(map(str, unknown))[:200]}")
    out: dict = dict.fromkeys(COLUMNS)
    for name in REQUIRED:
        if record.get(name) in (None, ""):
            raise ValueError(f"{name}: missing")
    for name in TEXT:
        value = record.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or len(value) > MAX_TEXT:
            raise ValueError(f"{name}: not a text of at most {MAX_TEXT} characters")
        out[name] = value
    if out["interface"] not in allowed:
        raise ValueError(f"interface: {out['interface']!r} is not one of {', '.join(sorted(allowed))}")
    for name in TIMES:
        if record.get(name) is not None:
            out[name] = _time(record[name], name)
    for name in COUNTS:
        value = record.get(name)
        if value is None:
            continue
        top = 2**31 if name in ("turns", "llm_calls", "tool_calls") else MAX_COUNT  # INTEGER columns
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < top:
            raise ValueError(f"{name}: not a count")
        out[name] = value
    tools = record.get("tool_breakdown")
    if tools is None:
        tools = {}
    if not isinstance(tools, dict) or len(tools) > MAX_TOOLS:
        raise ValueError(f"tool_breakdown: not an object of at most {MAX_TOOLS} entries")
    for key, n in tools.items():
        if not isinstance(key, str) or len(key) > MAX_TEXT or isinstance(n, bool) or not isinstance(n, int) or n < 0:
            raise ValueError("tool_breakdown: entries are name: count")
    out["tool_breakdown"] = tools
    return out


# ------------------------------------------------------------------ database


def pg_insert(records: list[dict]) -> int:
    """Insert the records; returns how many were new. Raises when the
    database cannot be reached. One connection per request: this is a few
    requests a minute at most, and a connection that is opened when needed
    cannot go stale over a database restart or a renewed ticket."""
    import psycopg
    from psycopg import sql
    from psycopg.types.json import Jsonb

    table = os.environ.get("LOG_PG_TABLE", "usage.sessions")
    query = sql.SQL("INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (session_id, logged_at) DO NOTHING").format(
        t=sql.Identifier(*table.split(".")), c=sql.SQL(", ").join(map(sql.Identifier, COLUMNS)),
        v=sql.SQL(", ").join(sql.Placeholder(c) for c in COLUMNS))
    new = 0
    # gssencmode=prefer explicitly: psycopg's binary build may default to
    # disable, and Kerberos is the only credential there is.
    with psycopg.connect(os.environ["LOG_PG_DSN"], connect_timeout=10, gssencmode="prefer") as conn:
        with conn.cursor() as cur:
            for rec in records:
                cur.execute(query, {**rec, "tool_breakdown": Jsonb(rec["tool_breakdown"])})
                new += max(cur.rowcount, 0)
    return new


def pg_count() -> int:
    import psycopg
    from psycopg import sql

    table = os.environ.get("LOG_PG_TABLE", "usage.sessions")
    with psycopg.connect(os.environ["LOG_PG_DSN"], connect_timeout=10, gssencmode="prefer") as conn:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT count(*) FROM {t}").format(t=sql.Identifier(*table.split("."))))
            return cur.fetchone()[0]


SUMMARY_SUMS = ("llm_calls", "tool_calls", "input_tokens", "output_tokens", "cache_read_tokens")


def pg_summary(days: int) -> dict:
    """The sessions whose last activity was in the last ``days`` days, from
    the view that holds each session's latest totals: per interface, provider
    and model, and per day (UTC) and interface. Turns are left out: the
    harnesses do not count them the same way."""
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row

    table = os.environ.get("LOG_PG_TABLE", "usage.sessions") + "_current"
    sums = sql.SQL(", ").join(
        sql.SQL("coalesce(sum({c}), 0)::bigint AS {c}").format(c=sql.Identifier(c)) for c in SUMMARY_SUMS)
    where = sql.SQL("WHERE session_updated_at > now() - make_interval(days => %s)")
    t = sql.Identifier(*table.split("."))
    by_model = sql.SQL(
        "SELECT interface, provider, model, count(*)::bigint AS sessions, {s} FROM {t} {w} "
        "GROUP BY 1, 2, 3 ORDER BY input_tokens DESC, sessions DESC").format(s=sums, t=t, w=where)
    by_day = sql.SQL(
        "SELECT (session_updated_at AT TIME ZONE 'UTC')::date::text AS day, interface, "
        "count(*)::bigint AS sessions, {s} FROM {t} {w} GROUP BY 1, 2 ORDER BY 1 DESC, 2").format(s=sums, t=t, w=where)
    with psycopg.connect(os.environ["LOG_PG_DSN"], connect_timeout=10, gssencmode="prefer",
                         row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            models = cur.execute(by_model, (days,)).fetchall()
            per_day = cur.execute(by_day, (days,)).fetchall()
    totals = {"sessions": sum(r["sessions"] for r in models)}
    totals.update({c: sum(r[c] for r in models) for c in SUMMARY_SUMS})
    return {"days": days, "totals": totals, "by_model": models, "by_day": per_day}


def mikey_verifier() -> Callable[[str], bool]:
    """True for a key of the mikey key file. The file is read on every call
    (it is small), so a key added or revoked counts at once."""
    from mikey.store import KeyStore

    path = os.environ.get("MIKEY_KEYS_FILE", "")
    if not path:
        raise SystemExit("usage-server: MIKEY_KEYS_FILE is not set (the mikey key file)")
    store = KeyStore(path)
    return lambda token: store.verify(token) is not None


# ----------------------------------------------------------------------- app


def create_app(verify: Callable[[str], bool], insert: Callable[[list[dict]], int],
               summary: Callable[[int], dict] | None = None) -> Starlette:
    """The service. ``verify`` says whether a bearer token is a valid key;
    ``insert`` stores cleaned records and returns how many were new;
    ``summary`` returns the usage of the last N days."""

    def has_key(request: Request) -> bool:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        return scheme.lower() == "bearer" and bool(token.strip()) and verify(token.strip())

    no_key = {"error": "a valid mikey key is needed (Authorization: Bearer ...)"}

    async def usage_summary(request: Request) -> JSONResponse:
        if not has_key(request):
            return JSONResponse(no_key, status_code=401)
        try:
            days = int(request.query_params.get("days", "30"))
        except ValueError:
            days = 0
        if not 1 <= days <= 366:
            return JSONResponse({"error": "days: a number from 1 to 366"}, status_code=400)
        if summary is None:
            return JSONResponse({"error": "no summary here"}, status_code=404)
        try:
            return JSONResponse(await run_in_threadpool(summary, days))
        except Exception as exc:  # noqa: BLE001
            log.warning("database: %s: %s", type(exc).__name__, exc)
            return JSONResponse({"error": "the database could not be reached; try again later"}, status_code=503)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "service": "mu2e-usage-server", "version": service_version()})

    async def index(request: Request) -> PlainTextResponse:
        return PlainTextResponse(
            "Mu2e AI usage log service.\n"
            "POST /usage takes usage records from the harnesses; GET /summary?days=30 gives the totals.\n"
            "Both need a Mu2e key. See aitools harness/usage/server.\n")

    async def usage(request: Request) -> JSONResponse:
        if not has_key(request):
            return JSONResponse(no_key, status_code=401)
        body = await request.body()
        if len(body) > MAX_BODY:
            return JSONResponse({"error": f"more than {MAX_BODY} bytes"}, status_code=413)
        try:
            data = json.loads(body)
        except ValueError:
            return JSONResponse({"error": "not JSON"}, status_code=400)
        records = data if isinstance(data, list) else [data]
        if not records or len(records) > MAX_RECORDS or (not isinstance(data, list) and not isinstance(data, dict)):
            return JSONResponse({"error": f"send one record, or a list of 1 to {MAX_RECORDS}"}, status_code=400)

        allowed = interfaces()
        good, rejected = [], []
        for i, rec in enumerate(records):
            try:
                good.append(clean(rec, allowed))
            except ValueError as exc:
                rejected.append({"index": i, "error": str(exc)})
        inserted = 0
        if good:
            try:
                inserted = await run_in_threadpool(insert, good)
            except Exception as exc:  # noqa: BLE001 - no ticket, database down: the sender keeps them
                log.warning("database: %s: %s", type(exc).__name__, exc)
                return JSONResponse({"error": "the database could not be reached; send again later"},
                                    status_code=503)
        # What was logged, never who logged it.
        log.info("%d received, %d new, %d rejected (%s)", len(records), inserted, len(rejected),
                 ", ".join(sorted({r["interface"] for r in good})) or "-")
        return JSONResponse({"received": len(records), "inserted": inserted, "rejected": rejected})

    return Starlette(routes=[
        Route("/", index, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route("/usage", usage, methods=["POST"]),
        Route("/summary", usage_summary, methods=["GET"]),
    ])


def check() -> int:
    ok = True
    print(f"mu2e-usage-server {service_version()}")
    path = os.environ.get("MIKEY_KEYS_FILE", "")
    try:
        from mikey.store import KeyStore

        n = len(KeyStore(path).list()) if path else 0
        print(f"keys:      {path or '(MIKEY_KEYS_FILE is not set)'}" + (f", {n} key(s)" if path else ""))
        ok = ok and bool(path) and n > 0
    except Exception as exc:  # noqa: BLE001
        print(f"keys:      FAILED: {type(exc).__name__}: {exc}")
        ok = False
    dsn = os.environ.get("LOG_PG_DSN", "")
    print(f"KRB5CCNAME: {os.environ.get('KRB5CCNAME') or '(unset: the default cache)'}")
    if not dsn:
        print("database:  LOG_PG_DSN is not set")
        return 1
    try:
        print(f"database:  ok, {os.environ.get('LOG_PG_TABLE', 'usage.sessions')} has {pg_count()} row(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"database:  FAILED: {type(exc).__name__}: {exc}")
        ok = False
    print(f"interfaces: {', '.join(sorted(interfaces()))}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Mu2e AI usage log service (see the module text: python -m pydoc usage_server.app)")
    parser.add_argument("--host", default=os.environ.get("USAGE_SERVER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("USAGE_SERVER_PORT", str(DEFAULT_PORT))))
    parser.add_argument("--check", action="store_true", help="try the key file and the database, print, exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("USAGE_SERVER_LOG_LEVEL", "INFO"), stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.check:
        sys.exit(check())
    if not os.environ.get("LOG_PG_DSN"):
        raise SystemExit("usage-server: LOG_PG_DSN is not set (e.g. postgresql://ifdb11:5477/mu2e_ai_prd)")
    import uvicorn

    app = create_app(mikey_verifier(), pg_insert, pg_summary)
    # No access log: it would record the address of every sender.
    uvicorn.run(app, host=args.host, port=args.port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
