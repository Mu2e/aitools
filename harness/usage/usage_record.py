"""Shared usage-record shape for the goose and claude-code loggers.

Records match the shared table ``usage.sessions`` (sessions.sql, next to
this file) column for column -- the same shape the Slack bot writes
(harness/slack/src/mu2e_slack/usage_log.py), so all three interfaces land
in one dataset. ``interface`` tells them apart.

Nothing that identifies a person is recorded: no user, no session name, no
working directory, no content. ``session_id`` is "<interface>-" plus a hash
of the tool's own session id, so a session's turns stay one row without
pointing back at a transcript.

Standard library only, Python 3.9+: the loggers run from hooks under the
system python3.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

# Column order of usage.sessions; also the key order of the jsonl records.
# Keep in sync with sessions.sql and the Slack bot's usage_log.COLUMNS.
COLUMNS = (
    "session_id", "interface", "harness_version", "host", "logged_at",
    "session_created_at", "session_updated_at", "provider", "endpoint_url", "model",
    "turns", "llm_calls", "tool_calls", "tool_breakdown",
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "thinking_tokens",
)


def session_id(interface: str, raw: str) -> str:
    """Opaque, stable id. Hashed with $USER because some tools' ids are only
    unique per user (goose numbers sessions "20260915_2")."""
    key = f"{os.environ.get('USER', '')}:{raw}"
    return f"{interface}-" + hashlib.sha256(key.encode()).hexdigest()[:16]


def provider_from_endpoint(url: Optional[str], default: str = "unknown") -> str:
    """"litellm" for https://litellm.fnal.gov/..., "vllm" for vllm.fnal.gov:
    the gateway, not the wire protocol -- same rule as the Slack bot."""
    host = urlparse(url or "").hostname or ""
    return host.split(".")[0] or default


def harness_commit() -> str:
    """aitools commit, 7 characters like the Slack bot writes it (the same
    commit at two widths would not group in SQL)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "--short=7", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return ""


def utc_iso(value) -> Optional[str]:
    """ISO-8601 with an explicit offset. A naive timestamp is taken as UTC
    (SQLite's CURRENT_TIMESTAMP, which goose uses, is UTC); without the
    offset a TIMESTAMPTZ column would read it as the server's local time."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def make_record(interface: str, raw_session_id: str, **fields) -> dict:
    """A record with every column, in table order. Unknown values stay None
    (NULL in the table), not 0 or ""."""
    rec = dict.fromkeys(COLUMNS)
    rec.update(
        session_id=session_id(interface, raw_session_id),
        interface=interface,
        harness_version=harness_commit(),
        host=socket.gethostname(),
        logged_at=datetime.now(timezone.utc).isoformat(),
    )
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"not a usage.sessions column: {sorted(unknown)}")
    rec.update(fields)
    if rec["tool_breakdown"] is None:
        rec["tool_breakdown"] = {}
    return rec


def write_jsonl(record: dict, path: Path) -> None:
    """Upsert one JSON line keyed by session_id (one line per session however
    many turns), via tmp-then-rename so a crash can't corrupt the log."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    new_line = json.dumps(record, default=str)
    lines, replaced = [], False
    if path.exists():
        with open(path) as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    same = json.loads(stripped).get("session_id") == record["session_id"]
                except (json.JSONDecodeError, AttributeError):
                    same = False
                lines.append((new_line if same else stripped) + "\n")
                replaced = replaced or same
    if not replaced:
        lines.append(new_line + "\n")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.writelines(lines)
    tmp.replace(path)
