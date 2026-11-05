#!/usr/bin/env python3
"""
log-session.py  --  extract per-session usage summary from goose's SQLite
                        and upsert it into a JSON-lines file.

Usage:
    log-session.py [SESSION_ID]

    SESSION_ID  The goose session id to harvest (e.g. "20260915_2").
                Defaults to the most-recently-updated session in the DB.

Output:
    Upserts one JSON line per session into $LOG_OUTPUT
    (default: $XDG_DATA_HOME/goose/log/usage.jsonl, next to sessions.db).

    Records have exactly the columns of the shared table usage.sessions
    (../usage/sessions.sql), interface "goose" -- the same shape the Slack
    bot and claude-code write; see ../usage/usage_record.py. No user,
    session name, working directory or content is recorded; session_id is a
    hash. (Writing to Postgres is not done here: users have no DB role. The
    Slack bot's usage_log.py has the writer.)
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "usage"))
import usage_record  # noqa: E402  (harness/usage/usage_record.py)


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

def _default_db() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    return Path(xdg) / "goose/sessions/sessions.db"


def _default_output() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share"
    return Path(xdg) / "goose/log/usage.jsonl"


DB_PATH      = Path(os.environ.get("LOG_DB",      _default_db()))
OUTPUT_FILE  = Path(os.environ.get("LOG_OUTPUT",  _default_output()))


# ---------------------------------------------------------------------------
# DATA EXTRACTION
# ---------------------------------------------------------------------------

def fetch_session(db: sqlite3.Connection, session_id: str) -> dict:
    """Pull everything we need from the SQLite DB for one session."""

    # -- sessions table -------------------------------------------------------
    row = db.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Session not found: {session_id!r}")

    s = dict(row)  # sqlite3.Row → plain dict

    # -- usage_ledger: aggregate across all LLM calls -------------------------
    ledger = db.execute("""
        SELECT
            COUNT(*)              AS llm_calls,
            SUM(input_tokens)     AS input_tokens,
            SUM(output_tokens)    AS output_tokens,
            SUM(cache_read_tokens)  AS cache_read_tokens,
            SUM(cache_write_tokens) AS cache_write_tokens,
            SUM(cost)             AS cost_usd   -- kept for ledger reconciliation if needed
        FROM usage_ledger
        WHERE session_id = ?
    """, (session_id,)).fetchone()

    # -- messages: count user turns and tool calls ----------------------------
    msg_rows = db.execute(
        "SELECT role, content_json FROM messages WHERE session_id = ?",
        (session_id,)
    ).fetchall()

    turns = 0
    tool_breakdown: dict[str, int] = {}

    for msg in msg_rows:
        role = msg["role"]
        if role == "user":
            turns += 1
        elif role == "assistant":
            try:
                content = json.loads(msg["content_json"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(content, list):
                content = [content]
            for item in content:
                if isinstance(item, dict) and item.get("type") == "toolRequest":
                    name = (item
                            .get("toolCall", {})
                            .get("value", {})
                            .get("name", "unknown"))
                    tool_breakdown[name] = tool_breakdown.get(name, 0) + 1

    tool_calls = sum(tool_breakdown.values())

    # -- model name from model_config_json ------------------------------------
    model = ""
    try:
        mcj = json.loads(s.get("model_config_json") or "{}")
        model = mcj.get("model_name", "")
    except (json.JSONDecodeError, TypeError):
        pass

    # -- endpoint URL: read from goose's live config.yaml ---------------------
    # Not stored in SQLite; lives in $XDG_CONFIG_HOME/goose/config.yaml as
    # OPENAI_BASE_URL (used by both the openai and litellm provider slots).
    endpoint_url = ""
    try:
        import yaml  # PyYAML -- available in most Python envs
        xdg_config = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
        config_yaml = Path(xdg_config) / "goose/config.yaml"
        if config_yaml.exists():
            with open(config_yaml) as fh:
                cfg = yaml.safe_load(fh) or {}
            endpoint_url = cfg.get("OPENAI_BASE_URL", "")
    except Exception:
        pass  # yaml not installed or file unreadable -- non-fatal

    # provider: the gateway from the endpoint ("vllm", "litellm", ...), like
    # the Slack bot -- goose's own provider_name is the protocol slot
    # ("openai" for vllm.fnal.gov and ALCF alike).
    return usage_record.make_record(
        "goose", s["id"],
        session_created_at=usage_record.utc_iso(s.get("created_at")),
        session_updated_at=usage_record.utc_iso(s.get("updated_at")),
        provider=usage_record.provider_from_endpoint(
            endpoint_url, default=s.get("provider_name") or "unknown"),
        endpoint_url=endpoint_url or None,
        model=model or None,
        turns=turns,
        llm_calls=int(ledger["llm_calls"] or 0),
        tool_calls=tool_calls,
        tool_breakdown=tool_breakdown,
        input_tokens=int(s.get("accumulated_input_tokens") or 0),
        output_tokens=int(s.get("accumulated_output_tokens") or 0),
        cache_read_tokens=int(s.get("accumulated_cache_read_tokens") or 0),
        cache_write_tokens=int(s.get("accumulated_cache_write_tokens") or 0),
        thinking_tokens=None,  # goose does not record it
    )


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    session_id = sys.argv[1] if len(sys.argv) > 1 else None

    if not DB_PATH.exists():
        print(f"ERROR: goose sessions DB not found: {DB_PATH}", file=sys.stderr)
        print("       Source env.sh first, or set LOG_DB.", file=sys.stderr)
        sys.exit(1)

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    if session_id is None:
        row = db.execute(
            "SELECT id FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print("ERROR: no sessions found in DB", file=sys.stderr)
            sys.exit(1)
        session_id = row["id"]
        print(f"No session ID given -- using most recent: {session_id}")

    record = fetch_session(db, session_id)
    db.close()

    # Pretty-print what we collected
    print(json.dumps(record, indent=2, default=str))
    print()

    usage_record.write_jsonl(record, OUTPUT_FILE)
    print(f"Upserted into: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
