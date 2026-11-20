# agentlog-mcp

A log of agent investigations, backed by Postgres and served over
streamable-HTTP with mikey auth.

The motivating flow: an agent is woken by an alarm (or a cron, or a human),
works the problem with whatever tools it has — dqm, runs, metacat, ecl, kb —
and then files a report here. Displays, digests, Slack summaries and later
agents read those reports back out.

## What makes this one different

Two things, and both shape the whole design:

**It writes, and it is append-only.** The database role holds `INSERT` and
`SELECT` and nothing else, on every table. There is no `update_report` and no
`delete_report` — not because they were forgotten, but because the grants
would refuse them. A correction, a follow-up or a human's comment is a **new
report** linked to the old one (`comment_on_report`). The original stays
readable, stays in every listing, and still reads exactly as it was filed.

**Nothing is ever hidden either.** A report that was later corrected is not
filtered out of anything; it comes back from `list_reports` with
`corrected_by` naming the report that corrected it, so a display can mark it.
A row that silently disappears from a log is worse than a row that is wrong.

Compare memory-mcp, the other writer in this workspace: there, documents are
private to the owner named by the token. Here there is **no owner boundary** —
every report is readable by anyone who can read the schema. `author` is the
authenticated key's name and is accountability, not access control. It is
never a tool argument.

## Subjects are an index, not a provenance record — read this part

`agentlog.refs` answers exactly one question: **which reports are about this
run / this night / this subsystem?** That is what `subjects` fills in, and it
is what makes a report findable later.

It is *not* a record of what the report consulted. "I called `dqm.list_alarms`
with these arguments" is prose in the body, where the agent explains its
reasoning anyway, and full-text search covers it. Nobody needs to query that
structurally.

This is not a style preference. A `refs` row is **shared between reports and
can never be updated**, so it can only hold facts about the thing itself. An
earlier draft of the schema tried to keep tool arguments in `refs.attrs`, and a
reviewer broke it in three steps: the second report to call the same tool hit
`ON CONFLICT DO NOTHING`, its arguments were discarded, and it was linked to
the *first* report's arguments — silently, unqueryably, and unrepairable
without `UPDATE`. The fix was to remove the second concept, not to widen the
key. See decision 1 in `sql/create_tables.sql`.

An `mcp:` subject is still legitimate, because a tool can be the *subject* of a
report: "`dqm.list_alarms` with this key is too slow". That is a report about
the tool, not a report that used it.

## Tools

| Tool | What it does |
|---|---|
| `get_server_info` | config, version, the calling author, and the vocabularies |
| `file_report` | file a report — title, body, subjects. Permanent. |
| `comment_on_report` | comment on / correct / continue an existing report |
| `list_reports` | find reports by run, time window, subject, severity, author, tags or full text. Never returns bodies. |
| `get_report` | one report in full, with its refs and its trail in both directions |
| `list_refs` | browse the index itself: which runs, nights, subsystems have reports |

### Writing a report

`body` is where the work goes: what fired, how it was checked, which tools were
called and what they returned, the conclusion and how confident the agent is.
There are deliberately **no columns** for any of that. An earlier draft had
`kind`, `verdict`, `confidence`, `model` and `session_id`; all five were cut,
because a reader wants the reasoning with its justification attached rather
than a bare enum, and nobody was ever going to query `confidence > 80`
(decision 12).

What survives as a column is what a display or digest filters on: `severity`,
`triggered_by`, `author`, `agent`, `tags`, plus `attrs` as a JSON escape hatch
for anything a downstream procedure would rather parse than regex out of prose.

### Subjects syntax

`<type>:<key>` entries, one per line (commas also work when no key contains
one; only the first colon splits, so URLs and ISO intervals survive intact):

```
run:10542                                        or run:10500-10600
time:2026-11-11T20:00:00Z/2026-11-12T08:00:00Z   or a bare instant
system:trk
metric:trk/track/meanChi2
alarm:trk/track/meanChi2@valNightly
dataset:mu2e:mcs.mu2e.CeEndpoint...
file:nts.mu2e....root
document:docdb:12345                             also ecl:67890, wiki:TrackerHV
url:https://...
report:418
mcp:dqm.list_alarms                              the report is ABOUT the tool
free:anything else
```

`mentions` takes the same syntax for a passing reference rather than the
subject.

### Canonicalization

**The dedup premise rests on this**, so it lives in one dependency-free module
(`src/agentlog_mcp/canon.py`) with a self-test:

```bash
./scripts/test_canon.py      # plain python3, no database, no venv needed
```

Run 10542 must always become `('run', '10542')` — not `Run 10542`, not
`010542` — or the index grows a long tail of near-duplicates and a search
quietly misses reports. Nothing fails loudly when this is wrong, and the MCP
cannot repair it afterwards (no `UPDATE`), which is why the rules are asserted
rather than just reviewed.

What is folded, and what deliberately is not:

| Type | Rule |
|---|---|
| `run` | prefix/zeros/underscores stripped, ranges normalized to `lo-hi`, reversed ranges ordered. Fills `run_lo`/`run_hi` as integers. |
| `time` | parsed to UTC, truncated to whole seconds, rendered `...Z`. Fills `time_lo`/`time_hi`. A bare date is midnight at the **start** of that day. |
| `system` | lower-cased |
| `metric`, `alarm` | whitespace around `/` removed; **case preserved** (`namex` is camelCase in `dqm.values`) |
| `dataset`, `file`, `mcp` | whitespace collapsed; case preserved |
| `document` | `DocDB 12345` → `docdb:12345`; source lower-cased, numeric ids de-zeroed |
| `url` | scheme and host lower-cased, path and query untouched |
| `report` | numeric id, de-zeroed |
| `free` | whitespace collapsed |

Two refs can share a key only if they are the same thing; `run:10542` and
`run:10542-10543` stay distinct.

## Reading

`list_reports` returns one row per report **without the body** — check
`body_size`, then `get_report`. Filters compose: `run=10542` plus
`ref=system:trk` means a report carrying both, not either. `min_severity` is
the digest filter; `text` is full-text over title + summary + body, ranked.

`get_report` also returns the trail: `incoming_links` (reports pointing at this
one — a `supersedes` there means a later report claims to correct it) and
`outgoing_links`.

## Database

Schema `agentlog`, three tables plus three views. **Kerberos-authenticated from
the ambient environment — there are no database credentials in any config file
or environment variable.**

**No endpoint is built in.** The host, port and database name are not recorded
anywhere in this package, and neither is the bind port: those are deployment
facts, not source. They come from `AGENTLOG_MCP_DB_HOST` / `_DB_PORT` /
`_DB_NAME` / `AGENTLOG_MCP_PORT` (or the matching `--db-*` / `--port` flags),
usually written into the systemd unit by
`scripts/agentlog-mcp-install-unit.sh`. The server refuses to start without
them rather than guessing.

Privileges are split deliberately. The server logs in as the plain login role,
which holds `SELECT` directly, so **reads run unelevated**. **Writes** run
inside a transaction under `SET LOCAL ROLE update_role` — `LOCAL`, so the
elevation reverts on commit or rollback and cannot leak to whichever request
picks up that pooled connection next. An accidental `INSERT` from a code path
meant only to read is then refused by the database instead of silently
succeeding, which is worth having where a stray write is permanent.

Filing a report is one transaction: the report, each deduplicated ref, and the
links. A report that committed without its subjects would be permanently
unfindable with no way to add them later.

The schema must exist before the server will start. It is **not** created
automatically — the MCP's role cannot create tables, and schema changes should
be a considered act:

```bash
psql "host=<db-host> port=<db-port> dbname=<db-name>" -f sql/create_tables.sql
```

`sql/create_tables.sql` carries its own design notes and a numbered
DESIGN DECISIONS block recording what was chosen deliberately — read that
before changing anything in it.

### Migrations

| File | When |
|---|---|
| `sql/migrations/001-fix-range-projection-checks.sql` | any schema created before 2026-11-19. Tightens `refs_run_cols`/`refs_time_cols` to `num_nonnulls`, closing a hole that accepted a half-filled run range on a non-run ref — a row no range query could ever match. Fresh runs of `create_tables.sql` already have it. |

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MIKEY_KEYS_FILE` | _(required)_ | mikey keys file — **server will not start without it** |
| `AGENTLOG_MCP_DB_HOST` | _(required)_ | Postgres host |
| `AGENTLOG_MCP_DB_PORT` | _(required)_ | Postgres port |
| `AGENTLOG_MCP_DB_NAME` | _(required)_ | database |
| `AGENTLOG_MCP_PORT` | _(required)_ | bind port |
| `AGENTLOG_MCP_HOST` | `0.0.0.0` | bind address |
| `AGENTLOG_MCP_DB_SCHEMA` | `agentlog` | schema |
| `AGENTLOG_MCP_WRITE_ROLE` | `update_role` | role assumed for inserts |
| `AGENTLOG_MCP_MAX_BODY_BYTES` | `1048576` | largest accepted report body |
| `AGENTLOG_MCP_DB_POOL_MAX` | `4` | connection pool size |
| `AGENTLOG_MCP_LOG_LEVEL` | `INFO` | logger level |
| `KRB5CCNAME` | _(system default)_ | Kerberos credential cache for the database connection — pin it via `--krb5-ccname` whenever a renewal job maintains a non-default cache |

Each has a matching CLI flag (`--db-host`, `--port`, `--write-role`, …); the
flag wins.

## `--check`

Verifies configuration without binding a socket: confirms the endpoint is
configured and `MIKEY_KEYS_FILE` is set, opens a real database connection,
confirms all three tables and `report_index` are present *and readable by the
unelevated login role*, and confirms the write role can actually be assumed
(inside a transaction forced to roll back, so `--check` files nothing — which
matters here, since a report written by a health check would be permanent).
Fails with a one-line message, exit 1. A Kerberos failure gets a diagnosis
naming the credential cache actually in use.

```bash
MIKEY_KEYS_FILE=/path/to/keys \
AGENTLOG_MCP_DB_HOST=<host> AGENTLOG_MCP_DB_PORT=<port> \
AGENTLOG_MCP_DB_NAME=<db> agentlog-mcp --check
```

## Server-account install

```bash
# as the account that will run the service, with uv on PATH:
./scripts/install.sh /exp/mu2e/app/users/<account>/mcp/agentlog <ref>

# first install only, as a member of admin_role:
psql "host=<db-host> port=<db-port> dbname=<db-name>" \
     -f <deploy-root>/current/.venv/share/agentlog-mcp/create_tables.sql

# then render + link the systemd --user unit:
<deploy-root>/current/.venv/bin/agentlog-mcp-install-unit.sh \
    --mikey-keys-file /path/to/keys \
    --port <port> --db-host <host> --db-port <port> --db-name <db> \
    --krb5-ccname FILE:/tmp/krb5cc_<uid>_auto
```

Pass the endpoint via `--env-file` instead if you would rather it live in one
file outside the deploy tree; the install-unit script checks that every
required value is available from one source or the other before writing a unit
that would otherwise fail at startup.

Requires linger enabled for the account (`loginctl enable-linger`) so the
service survives logout.

## Smoke test

```bash
<deploy-root>/current/.venv/bin/python scripts/smoke_test_http.py \
    http://<host>:<port> mikey_<token>          # read-only
```

Read-only by default, and that default matters more here than in memory-mcp:
`--write` files **permanent, publicly readable** reports that nothing but a
database administrator can remove. What it leaves behind is deliberately
obvious (`[smoketest]` titles, `smoketest` tag, a `free:agentlog-smoketest`
ref, and a `run:1` ref to prove the run projection and range query). Run it
with `--write` once against a new deployment, read-only after that.

## Client config

```bash
claude mcp add --transport http --scope user agentlog http://<host>:<port>/mcp \
  --header "Authorization: Bearer <mikey-token>"
```

The port this deployment uses is recorded once, in
`registry/config/ports.json` (as `agentlog`), which is what the registry
serves and what `scripts/write_joint_registry.sh` builds client configs from.
It is deliberately not repeated in this package — the server takes it from
`--port` / `AGENTLOG_MCP_PORT` at startup.

`--scope user` or `--scope local` — never `--scope project` (see mikey's
README). Unlike memory-mcp, the token does not partition what you can read: it
names the author your reports are filed under.

## Noted for future work

- **No human-facing display.** The views (`report_index`, `report_refs_full`,
  `report_links`) exist precisely so a display does not re-derive the joins,
  but nothing consumes them yet. A browser-facing page needs an
  authentication story first, same as memory-mcp's.
- **Digest/Slack consumption.** `min_severity` + `since` + `order_by=severity`
  is the query a nightly digest wants; wiring it to the Slack bot is not done.
- **`role` in the link primary key.** The schema keeps `role` in
  `report_refs`'s PK, but with the trimmed five-role vocabulary the surviving
  roles are mutually exclusive in practice, so `(report_id, ref_id)` alone is
  now defensible (decision 5).
- **Dropped ref types.** `subrun` and `agent` were cut; either is a one-line
  `ALTER` away if a real need turns up (decision 2).
