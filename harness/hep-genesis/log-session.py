#!/usr/bin/env python3
"""hep-genesis usage logger: one record per chat, in the shared format.

hep-genesis runs this after every turn (its HEP_TURN_HOOK, set by env.sh and
the systemd units):

    log-session.py <project dir> <chat name>

It reads the chat's lines from <project>/provenance/usage.jsonl, which
hep-genesis writes itself (one line per turn: model calls, tokens, tool
names), adds them up, and writes the chat's running totals as one record in
the shape of the shared table usage.sessions -- the one the Slack bot, goose
and claude-code write; see ../usage/usage_record.py. interface is
"hep-genesis". No user, no project or chat name, no content: session_id is
a hash of the project folder and the chat.

Where it goes:
    LOG_OUTPUT   a jsonl file, one line per chat, rewritten in place
                 (default: <data folder>/log/usage.jsonl). Always written.
    LOG_PG_DSN   set: also appended to Postgres (LOG_PG_TABLE, default
                 usage.sessions; insert-only), e.g.
                 "postgresql://ifdb11:5477/mu2e_ai_prd" with Kerberos
                 (KRB5CCNAME) as the credential. Needs psycopg in the python
                 that runs this. A record that cannot be sent waits in
                 pg-backlog.jsonl next to LOG_OUTPUT and goes ahead of the
                 next one that can -- as the Slack bot does it.

    log-session.py --check     say where records go, and try Postgres

A failure here never costs the user their answer: hep-genesis does not wait
for this script, and its output goes to <project>/provenance/turn-hook.log.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# In the aitools checkout the shared module is in ../usage; an installed
# release has it next to this file.
sys.path[:0] = [str(HERE), str(HERE.parent / "usage")]
import usage_record  # noqa: E402

INTERFACE = "hep-genesis"
PG_TABLE = os.environ.get("LOG_PG_TABLE", "usage.sessions")


def harness_version() -> str:
    """The aitools commit, 7 characters like the other harnesses write it. An
    installed release is not a checkout: install.sh wrote it to VERSION."""
    for line in (HERE.parent / "VERSION").read_text().splitlines() if (HERE.parent / "VERSION").is_file() else []:
        if line.startswith("aitools "):
            return line.split()[1][:7]
    return usage_record.harness_commit()


def output_file(project_dir: Path) -> Path:
    """LOG_OUTPUT, else <data folder>/log/usage.jsonl: a project is
    <data folder>/projects/<slug>."""
    explicit = os.environ.get("LOG_OUTPUT", "").strip()
    return Path(explicit) if explicit else project_dir.resolve().parent.parent / "log" / "usage.jsonl"


def _iso(ts) -> str | None:
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat() if ts else None


def chat_turns(project_dir: Path, chat: str) -> list[dict]:
    turns = []
    try:
        with (project_dir / "provenance" / "usage.jsonl").open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and (rec.get("chat") or "") == chat:
                    turns.append(rec)
    except OSError:
        pass
    return turns


def build_record(project_dir: Path, chat: str, turns: list[dict]) -> dict:
    """The chat's totals over its turns. hep-genesis counts the cached part
    inside "input" and the reasoning part inside "output", as the Slack bot
    does for input_tokens/cache_read_tokens. The model and endpoint are the
    latest turn's (a research run: where most of its input went)."""
    def total(key: str) -> int:
        return sum(int((t.get("usage") or {}).get(key) or 0) for t in turns)

    tools: dict[str, int] = {}
    for t in turns:
        for name, n in (t.get("tools") or {}).items():
            tools[name] = tools.get(name, 0) + int(n or 0)
    last = turns[-1]
    return usage_record.make_record(
        INTERFACE, f"{project_dir.resolve()}:{chat}",
        harness_version=harness_version(),
        session_created_at=_iso(min(t.get("started") or t.get("ts") for t in turns)),
        session_updated_at=_iso(max(t.get("ts") or 0 for t in turns)),
        provider=usage_record.provider_from_endpoint(last.get("endpoint_url")),
        endpoint_url=last.get("endpoint_url"),
        model=last.get("model"),
        turns=len(turns),
        llm_calls=total("calls"),
        tool_calls=sum(tools.values()),
        tool_breakdown=tools,
        input_tokens=total("input"),
        output_tokens=total("output"),
        cache_read_tokens=total("cached"),
        thinking_tokens=total("reasoning"),
    )


# ------------------------------------------------------------------ postgres


def _connect(dsn: str):
    import psycopg

    # gssencmode=prefer explicitly: psycopg's binary build may default to
    # disable, and Kerberos is the only credential there is.
    return psycopg.connect(dsn, connect_timeout=10, gssencmode="prefer")


def _insert(conn, record: dict) -> None:
    """Append one row. The table is insert-only: a session is a series of rows
    of running totals, and re-sending one is a no-op."""
    from psycopg import sql
    from psycopg.types.json import Jsonb

    cols = usage_record.COLUMNS
    query = sql.SQL("INSERT INTO {t} ({c}) VALUES ({v}) ON CONFLICT (session_id, logged_at) DO NOTHING").format(
        t=sql.Identifier(*PG_TABLE.split(".")), c=sql.SQL(", ").join(map(sql.Identifier, cols)),
        v=sql.SQL(", ").join(sql.Placeholder(c) for c in cols))
    params = {c: record.get(c) for c in cols}
    params["tool_breakdown"] = Jsonb(params["tool_breakdown"] or {})
    with conn.cursor() as cur:
        cur.execute(query, params)


def write_postgres(record: dict | None, dsn: str, backlog: Path) -> int:
    """Send the backlog, then ``record``; on success the backlog is gone.
    Returns how many backlog rows were cleared. Raises when the database
    cannot be reached (the caller then queues the record)."""
    import psycopg

    pending = []
    if backlog.exists():
        for line in backlog.read_text().splitlines():
            try:
                pending.append(json.loads(line))
            except ValueError:
                pass
    with _connect(dsn) as conn:
        for row in pending:
            try:
                with conn.transaction():  # a savepoint: one row the table refuses is not all of them
                    _insert(conn, row)
            except psycopg.OperationalError:
                raise
            except psycopg.Error as exc:
                print(f"dropping a backlog row the table refuses ({row.get('session_id')}): {exc}", file=sys.stderr)
        if record is not None:
            _insert(conn, record)
    if pending:
        backlog.unlink(missing_ok=True)
    return len(pending)


def send(record: dict, out: Path) -> None:
    dsn = os.environ.get("LOG_PG_DSN", "").strip()
    if not dsn:
        return
    backlog = out.with_name("pg-backlog.jsonl")
    try:
        cleared = write_postgres(record, dsn, backlog)
        if cleared:
            print(f"sent {cleared} record(s) that were waiting", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001 - no driver, no ticket, database down: it waits
        fd = os.open(backlog, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        print(f"{record['logged_at']} not sent to Postgres, kept in {backlog}: {type(exc).__name__}: {exc}",
              file=sys.stderr)


# ---------------------------------------------------------------------- main


def check() -> int:
    data = os.environ.get("HG_DATA_DIR") or (str(Path(os.environ["HEP_PROJECTS_DIR"]).parent)
                                             if os.environ.get("HEP_PROJECTS_DIR") else "")
    out = Path(os.environ["LOG_OUTPUT"]) if os.environ.get("LOG_OUTPUT") else (
        Path(data) / "log" / "usage.jsonl" if data else None)
    print(f"interface:  {INTERFACE}, harness {harness_version() or '(unknown)'}")
    print(f"jsonl file: {out or '<data folder>/log/usage.jsonl (no data folder in this environment)'}")
    dsn = os.environ.get("LOG_PG_DSN", "").strip()
    if not dsn:
        print("postgres:   off (LOG_PG_DSN is not set)")
        return 0
    try:
        from psycopg import sql

        with _connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT count(DISTINCT session_id) FROM {t} WHERE interface = {i}").format(
                t=sql.Identifier(*PG_TABLE.split(".")), i=sql.Literal(INTERFACE)))
            print(f"postgres:   ok, {PG_TABLE} has {cur.fetchone()[0]} {INTERFACE} session(s)")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"postgres:   FAILED: {type(exc).__name__}: {exc}")
        return 1


def main(argv: list[str]) -> int:
    if argv[1:] == ["--check"]:
        return check()
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    project_dir, chat = Path(argv[1]), argv[2]
    turns = chat_turns(project_dir, chat)
    if not turns:
        print(f"no usage lines for chat {chat!r} in {project_dir}/provenance/usage.jsonl", file=sys.stderr)
        return 1
    record = build_record(project_dir, chat, turns)
    out = output_file(project_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    # Turns of different chats can end together, and both the jsonl rewrite and
    # the backlog are read-modify-replace: one writer at a time.
    with (out.parent / ".usage.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        usage_record.write_jsonl(record, out)
        send(record, out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
