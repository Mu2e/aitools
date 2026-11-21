# Usage log service

Takes the harnesses' usage records and inserts them into the shared table
`usage.sessions` ([`../sessions.sql`](../sessions.sql)). Users have no role
in that database; the service runs as `mu2eai`, which has, on
`mu2eaigpvm01:8199`. The Slack bot and the hep-genesis service run as
`mu2eai` themselves and write to the database directly.

## For users

Claude Code, goose and hep-genesis send one record per session: model,
endpoint, counts (turns, model calls, tokens), tool names, host, harness
version, an opaque session id. No user, directory or content, and the
service stores nothing about who sent it.

```bash
python3 harness/usage/usage_record.py --check          # address, key found?, service up?
python3 harness/usage/usage_record.py --summary 30     # the usage of the last 30 days, all harnesses
```

- **Off:** `LOG_URL=` (empty) before sourcing a harness's `env.sh`.
- **Service down:** the record waits in `service-backlog.jsonl` next to your
  `usage.jsonl` and goes with a later one. A session never waits for it.

## Interface

| | |
|---|---|
| `POST /usage` | one record or a list (up to 500), shaped as [`../usage_record.py`](../usage_record.py) makes them; `Authorization: Bearer <Mu2e key>` (the one from `getMu2eKey` works) |
| `GET /summary?days=30` | totals of the last 1 to 366 days as JSON (`totals`, `by_model`, `by_day`), from `usage.sessions_current`; the same key |
| `GET /health` | `{"ok": true, "version": ...}`, no key |

`POST` answers `{"received", "inserted", "rejected"}`. A record of the wrong
shape is listed under `rejected` with the reason; the others are inserted.
Sending a record twice is harmless. `401` no valid key, `400` not a record
or list, `413` too large, `503` database unreachable (the sender keeps it).

## Deploy (`mu2eai` on `mu2eaigpvm01`)

Needs `uv` on `PATH`. Key file and Kerberos cache are the ones of the memory
server: `systemctl --user cat memory-mcp | grep -E 'MIKEY_KEYS_FILE|KRB5CCNAME'`.

```bash
deploy=/exp/mu2e/app/users/mu2eai/usage-server
harness/usage/server/scripts/install.sh $deploy <ref>

MIKEY_KEYS_FILE=<keys> KRB5CCNAME=<cache> LOG_PG_DSN=postgresql://ifdb11:5477/mu2e_ai_prd \
  $deploy/current/.venv/bin/usage-server --check

$deploy/current/.venv/bin/usage-server-install-unit.sh --mikey-keys-file <keys> --krb5-ccname <cache>
curl -s http://localhost:8199/health
journalctl --user -u usage-server -f     # per request: how many records, which interfaces
```

Upgrade: `install.sh` with the new ref, then `systemctl --user restart usage-server`.

## Tests

```bash
cd harness/usage/server && uv venv && uv pip install -e '.[dev]'
.venv/bin/python -m pytest tests        # no database, no network
```
