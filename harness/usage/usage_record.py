"""Shared usage-record shape for the goose and claude-code loggers.

Records match the shared table ``usage.sessions`` (sessions.sql, next to
this file) column for column -- the same shape the Slack bot writes
(harness/slack/src/mu2e_slack/usage_log.py), so all three interfaces land
in one dataset. ``interface`` tells them apart.

Nothing that identifies a person is recorded: no user, no session name, no
working directory, no content. ``session_id`` is "<interface>-" plus a hash
of the tool's own session id, so a session's turns stay one row without
pointing back at a transcript.

A logger writes its record to a local jsonl file (write_jsonl) and hands it
to the usage log service (send), which inserts it into the shared table:
users have no role in that database. See server/ next to this file.

    python3 usage_record.py --check     where records go; tries the service

Standard library only, Python 3.9+: the loggers run from hooks under the
system python3.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    if host == "alcf.anl.gov" or host.endswith(".alcf.anl.gov"):
        return "alcf"   # inference-api.alcf.anl.gov: the gateway is ALCF
    return host.split(".")[0] or default


def harness_commit() -> str:
    """aitools commit, 7 characters like the Slack bot writes it (the same
    commit at two widths would not group in SQL).

    A deployed copy (CVMFS) has no .git: deploy.sh writes the commit into
    harness/VERSION, which is read first."""
    try:
        version = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
        if version:
            return version[:7]
    except OSError:
        pass
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


# ------------------------------------------------------- the usage log service

BACKLOG_MAX = 2000        # records kept while the service cannot be reached
SEND_BATCH = 400          # the service takes up to 500 at once
RETRY_AFTER = 300         # seconds without a new attempt after a failed one
LOCK_WAIT = 6.0           # seconds a logger waits for another one to finish


def log_url() -> str:
    """$LOG_URL: where the usage log service takes records (the harness
    env.sh sets it). Empty or unset: nothing is sent."""
    return os.environ.get("LOG_URL", "").strip()


def mikey_token() -> str:
    """The Mu2e key the service asks for, found as shared/mikey-token.sh
    finds it: $MIKEY_TOKEN, a personal file, else getMu2eKey (CVMFS, or the
    copy in ../shared). "" when there is none."""
    token = os.environ.get("MIKEY_TOKEN", "").strip()
    if token:
        return token
    try:
        token = Path(f"/exp/mu2e/app/users/{os.environ.get('USER', '')}/mikey_token").read_text().strip()
        if token:
            return token
    except OSError:
        pass
    for getkey in ("/cvmfs/mu2e.opensciencegrid.org/bin/getMu2eKey",
                   str(Path(__file__).resolve().parent.parent / "shared" / "getMu2eKey")):
        if os.access(getkey, os.X_OK):
            try:
                out = subprocess.run([getkey], capture_output=True, text=True, timeout=10)
                if out.returncode == 0 and out.stdout.strip():
                    return out.stdout.strip().splitlines()[-1].strip()
            except Exception:
                pass
    return ""


def _post(url: str, token: str, records: list, timeout: float) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(records, default=str).encode(), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode() or "{}")


def _read_lines(path: Path) -> list:
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    except OSError:
        pass
    return out


def send(record: dict, backlog: Path, timeout: float = 3.0, retry_after: float = RETRY_AFTER) -> bool:
    """Hand ``record`` to the usage log service, after the records that are
    still waiting in ``backlog`` (a jsonl file next to the usage log). True
    when everything was taken. When the service cannot be reached the record
    is added to the backlog and goes with the next one that gets through;
    sending one twice is harmless (the table is insert-only on session_id +
    logged_at). After a failed attempt the next ``retry_after`` seconds'
    records are only added to the backlog, without trying again: the loggers
    run after every turn, and a service that is down must not cost each turn
    a timeout. Never raises, and says nothing unless $LOG_DEBUG is set."""
    url = log_url()
    if not url:
        return False
    backlog = Path(backlog)
    debug = bool(os.environ.get("LOG_DEBUG"))
    # One logger at a time reads, sends and clears the backlog: a harness can
    # run two within a moment (end of turn, end of session), and without this
    # one could delete a record the other had just put there.
    lock = None
    try:
        backlog.parent.mkdir(parents=True, exist_ok=True)
        lock = open(backlog.with_name("." + backlog.name + ".lock"), "a")
        deadline = time.time() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() > deadline:
                    break             # the other one is stuck: go on rather than lose the record
                time.sleep(0.05)
    except OSError:
        pass
    try:
        return _send_locked(record, backlog, url, timeout, retry_after, debug)
    finally:
        if lock is not None:
            lock.close()              # closing releases the lock


def _send_locked(record: dict, backlog: Path, url: str, timeout: float, retry_after: float, debug: bool) -> bool:
    records = _read_lines(backlog) + [record]
    try:
        if len(records) > 1 and time.time() - backlog.stat().st_mtime < retry_after:
            raise RuntimeError(f"not tried: the last attempt failed less than {int(retry_after)} s ago")
        token = mikey_token()
        if not token:
            raise RuntimeError("no Mu2e key (MIKEY_TOKEN, mikey_token file, getMu2eKey)")
        for i in range(0, len(records), SEND_BATCH):
            answer = _post(url, token, records[i:i + SEND_BATCH], timeout)
            for bad in answer.get("rejected") or []:
                # The service will never take these: not kept, or they would wait for good.
                print(f"usage log: a record was not accepted: {bad.get('error')}", file=sys.stderr)
        try:
            backlog.unlink()
        except OSError:
            pass
        return True
    except Exception as exc:  # noqa: BLE001 - service down, no key, a timeout: it waits
        if debug:
            print(f"usage log: not sent to {url}, kept in {backlog}: {type(exc).__name__}: {exc}", file=sys.stderr)
        try:
            backlog.parent.mkdir(parents=True, exist_ok=True)
            lines = [json.dumps(r, default=str) + "\n" for r in records[-BACKLOG_MAX:]]
            tmp = backlog.with_name(backlog.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.writelines(lines)
            tmp.replace(backlog)
        except OSError:
            pass
        return False


def backlog_path(jsonl: Path) -> Path:
    """The file of records waiting for the service, next to a usage log."""
    return Path(jsonl).with_name("service-backlog.jsonl")


def _check() -> int:
    url = log_url()
    print(f"service:  {url or '(LOG_URL is not set: nothing is sent)'}")
    if not url:
        return 0
    print(f"key:      {'found' if mikey_token() else 'NOT FOUND (MIKEY_TOKEN, mikey_token file, getMu2eKey)'}")
    health = url.rsplit("/", 1)[0] + "/health"
    try:
        with urllib.request.urlopen(health, timeout=5) as resp:
            info = json.loads(resp.read().decode())
        print(f"health:   ok, version {info.get('version')}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"health:   FAILED ({health}): {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--check"]:
        sys.exit(_check())
    print(__doc__, file=sys.stderr)
    sys.exit(2)
