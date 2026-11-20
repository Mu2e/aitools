"""The usage log service and the loggers' side of it, without a database.

    python -m pytest harness/usage/server/tests
"""

import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from starlette.testclient import TestClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))          # harness/usage: usage_record.py
import usage_record  # noqa: E402

from usage_server import app as usage_app  # noqa: E402

KEY = "mikey_test"
AUTH = {"Authorization": f"Bearer {KEY}"}


class Store:
    """Stands in for the database: keeps rows, drops repeats, can be down."""

    def __init__(self):
        self.rows, self.down = {}, False

    def insert(self, records):
        if self.down:
            raise RuntimeError("database is down")
        new = 0
        for r in records:
            key = (r["session_id"], r["logged_at"])
            new += key not in self.rows
            self.rows.setdefault(key, r)
        return new


@pytest.fixture
def store():
    return Store()


@pytest.fixture
def client(store):
    return TestClient(usage_app.create_app(lambda t: t == KEY, store.insert))


def record(**over):
    rec = usage_record.make_record("goose", "s1", model="m", turns=1, llm_calls=2, tool_calls=1,
                                   tool_breakdown={"runs__list_runs": 1}, input_tokens=10, output_tokens=5)
    rec.update(over)
    return rec


def test_columns_are_those_of_the_loggers():
    assert usage_app.COLUMNS == usage_record.COLUMNS
    assert set(usage_app.TEXT + usage_app.TIMES + usage_app.COUNTS + ("tool_breakdown",)) == set(usage_app.COLUMNS)


def test_health_needs_no_key(client):
    assert client.get("/health").json()["ok"] is True


def test_a_record_is_inserted_once(client, store):
    rec = record()
    assert client.post("/usage", json=rec, headers=AUTH).json() == {"received": 1, "inserted": 1, "rejected": []}
    assert client.post("/usage", json=rec, headers=AUTH).json()["inserted"] == 0     # the same row again
    (row,) = store.rows.values()
    assert row["tool_breakdown"] == {"runs__list_runs": 1} and row["cache_write_tokens"] is None
    assert set(row) == set(usage_app.COLUMNS)                                        # nothing about the sender


def test_no_key_or_a_wrong_key_is_refused(client, store):
    assert client.post("/usage", json=record()).status_code == 401
    assert client.post("/usage", json=record(), headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/usage", json=record(), headers={"Authorization": KEY}).status_code == 401
    assert not store.rows


@pytest.mark.parametrize("change, needle", [
    ({"interface": "other"}, "interface"),
    ({"user": "someone"}, "not a column"),
    ({"session_id": None}, "session_id"),
    ({"logged_at": "yesterday"}, "logged_at"),
    ({"logged_at": "2026-01-01T00:00:00"}, "offset"),
    ({"logged_at": "2999-01-01T00:00:00+00:00"}, "future"),
    ({"turns": -1}, "turns"),
    ({"turns": "3"}, "turns"),
    ({"input_tokens": True}, "input_tokens"),
    ({"model": "x" * 600}, "model"),
    ({"tool_breakdown": {"a": "b"}}, "tool_breakdown"),
    ({"tool_breakdown": [1]}, "tool_breakdown"),
])
def test_a_wrong_record_is_rejected_with_the_reason(client, store, change, needle):
    out = client.post("/usage", json=record(**change), headers=AUTH).json()
    assert out["inserted"] == 0 and needle in out["rejected"][0]["error"]
    assert not store.rows


def test_one_bad_record_does_not_hold_up_the_others(client, store):
    batch = [record(), record(turns=-1), record(session_id="goose-other")]
    out = client.post("/usage", json=batch, headers=AUTH).json()
    assert (out["received"], out["inserted"]) == (3, 2) and out["rejected"][0]["index"] == 1


def test_what_is_not_a_record_or_a_list(client):
    assert client.post("/usage", content=b"not json", headers=AUTH).status_code == 400
    assert client.post("/usage", json=[], headers=AUTH).status_code == 400
    assert client.post("/usage", json="text", headers=AUTH).status_code == 400
    assert client.post("/usage", json=[record()] * (usage_app.MAX_RECORDS + 1), headers=AUTH).status_code == 400
    assert client.post("/usage", content=b"[" + b" " * usage_app.MAX_BODY + b"]", headers=AUTH).status_code == 413


def test_database_down_is_503(client, store):
    store.down = True
    assert client.post("/usage", json=record(), headers=AUTH).status_code == 503


# ---- the loggers' side: usage_record.send against a running service


@pytest.fixture
def service(store, monkeypatch):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(usage_app.create_app(lambda t: t == KEY, store.insert),
                                           host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    monkeypatch.setenv("LOG_URL", f"http://127.0.0.1:{port}/usage")
    monkeypatch.setenv("MIKEY_TOKEN", KEY)
    yield store
    server.should_exit = True
    thread.join(5)


def test_send_and_the_backlog(service, tmp_path, monkeypatch):
    backlog = usage_record.backlog_path(tmp_path / "usage.jsonl")
    assert backlog.name == "service-backlog.jsonl"
    assert usage_record.send(record(), backlog) is True
    assert len(service.rows) == 1 and not backlog.exists()

    # The service cannot take it: the record waits, privately.
    service.down = True
    assert usage_record.send(record(session_id="goose-2"), backlog) is False
    assert usage_record.send(record(session_id="goose-3"), backlog, retry_after=0) is False
    assert len(backlog.read_text().splitlines()) == 2 and (backlog.stat().st_mode & 0o777) == 0o600

    # Back again, but the last attempt has only just failed: not tried, kept.
    service.down = False
    assert usage_record.send(record(session_id="goose-3b"), backlog) is False
    assert len(backlog.read_text().splitlines()) == 3 and "goose-2" not in {k[0] for k in service.rows}

    # Later: the waiting ones go first, and the backlog is gone.
    assert usage_record.send(record(session_id="goose-4"), backlog, retry_after=0) is True
    assert {k[0] for k in service.rows} >= {"goose-2", "goose-3", "goose-3b", "goose-4"} and not backlog.exists()

    # A wrong key: kept. No LOG_URL: nothing is sent, nothing is kept.
    monkeypatch.setenv("MIKEY_TOKEN", "wrong")
    assert usage_record.send(record(session_id="goose-5"), backlog, retry_after=0) is False and backlog.exists()
    backlog.unlink()
    monkeypatch.setenv("LOG_URL", "")
    assert usage_record.send(record(session_id="goose-6"), backlog) is False and not backlog.exists()


def test_a_rejected_record_does_not_wait_for_good(service, tmp_path, capsys):
    backlog = tmp_path / "service-backlog.jsonl"
    assert usage_record.send(record(interface="other"), backlog) is True
    assert not backlog.exists() and "not accepted" in capsys.readouterr().err


def test_nothing_listening_keeps_the_record(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_URL", "http://127.0.0.1:9/usage")
    monkeypatch.setenv("MIKEY_TOKEN", KEY)
    backlog = tmp_path / "service-backlog.jsonl"
    assert usage_record.send(record(), backlog, timeout=2) is False
    assert json.loads(backlog.read_text())["interface"] == "goose"
