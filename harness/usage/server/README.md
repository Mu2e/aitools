# Usage log service

A small HTTP service that takes the harnesses' usage records and inserts
them into the shared table `usage.sessions` ([`../sessions.sql`](../sessions.sql)).

Why: users have no role in that database, so the loggers of Claude Code,
goose and hep-genesis could only write a local `usage.jsonl`. The service
runs as `mu2eai`, which can insert, on `mu2eaigpvm01`, port 8199. The Slack
bot and the hep-genesis service, which run as `mu2eai` themselves, keep
writing to the database directly.

## What is sent

One record per session, in the shape of [`../usage_record.py`](../usage_record.py):
model, endpoint, counts of turns, model calls and tokens, tool names with
counts, the host, the harness version, and an opaque session id. No user,
no directory, no session name, no content. The service stores the record as
it is and nothing about who sent it: no key name, no address, no access log.

For users this is on by default (`LOG_URL`, set by each harness's `env.sh`).
To keep your records local, set `LOG_URL=` (empty) before sourcing `env.sh`.

```bash
python3 harness/usage/usage_record.py --check    # where records go, key found?, service reachable?
```

When the service cannot be reached, the record waits in
`service-backlog.jsonl` next to your `usage.jsonl` and goes with the next
one that gets through. A session never waits more than a few seconds for it.

## The interface

| | |
|---|---|
| `POST /usage` | one record or a list (up to 500), with `Authorization: Bearer <Mu2e key>` (mikey; the key from `getMu2eKey` works) |
| `GET /health` | `{"ok": true, "version": ...}`, no key |

The answer to a `POST` is `{"received", "inserted", "rejected"}`. A record
that is not in the right shape is listed under `rejected` with the reason;
the others are inserted all the same. Rows are insert-only on
`(session_id, logged_at)`, so sending one twice is harmless. `401` no valid
key, `400` not a record or a list, `413` too large, `503` the database could
not be reached (the sender keeps the records).

## Deploy (as `mu2eai` on `mu2eaigpvm01`)

Same layout as the MCP servers: release folders, a `current` link, a
`systemd --user` unit.

```bash
deploy=/exp/mu2e/app/users/mu2eai/usage-server
harness/usage/server/scripts/install.sh $deploy <ref>

# key file and Kerberos cache: the ones the memory server's unit uses
#   systemctl --user cat memory-mcp | grep -E 'MIKEY_KEYS_FILE|KRB5CCNAME'
MIKEY_KEYS_FILE=<keys file> LOG_PG_DSN=postgresql://ifdb11:5477/mu2e_ai_prd \
  KRB5CCNAME=<cache> $deploy/current/.venv/bin/usage-server --check

$deploy/current/.venv/bin/usage-server-install-unit.sh \
  --mikey-keys-file <keys file> --krb5-ccname <cache>
curl -s http://localhost:8199/health
journalctl --user -u usage-server -f      # one line per request: how many records, which interfaces
```

An upgrade is `install.sh` with the new ref, then
`systemctl --user restart usage-server`.

## Tests

```bash
cd harness/usage/server
uv venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest tests        # no database, no network
```

## Later

The same service is the natural place for an overview page (`GET /`):
sessions and tokens per interface, model and day, read from
`usage.sessions_current`.
