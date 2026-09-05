# memory-mcp

Persistent project memory for agents: a streamable-HTTP MCP server that stores
documents in Postgres and serves them back later — across sessions, and (the
case this is really built for) across ephemeral per-session sandboxes that keep
no disk of their own. mikey-authenticated, port 8007.

## What makes this one different

Two things separate it from the other servers in `aitools/mcp`, and both are
deliberate:

- **It writes.** Every other MCP here is read-only. Writes are append-only all
  the way down: the database role has `INSERT` and `SELECT` and nothing else.
  A new document version is a new row; a metadata change is a new row. Nothing
  this server can do modifies or deletes existing data.
- **mikey auth is mandatory, not optional.** In `ecl-mcp`/`runs-mcp`, an unset
  `MIKEY_KEYS_FILE` just means "no auth." Here it would mean "no owner" — and
  the owner *is* the access-control boundary. The server refuses to start
  without it, and every tool fails closed if a request arrives with no
  identity.

## Ownership and access control — read this part

A document's identity is `(owner, project, name)`. **`owner` comes from the
mikey key's name and is never a tool parameter.** It is injected server-side
into every query, so there is no code path — not even an accidental one — by
which a caller can name someone else's namespace.

That matters because **Postgres is not enforcing the isolation.** Every caller
shares one database role. This process is the only thing standing between one
owner's documents and another's; a query missing its `owner` binding would be
a cross-owner leak. All database access goes through helpers that bind `owner`
for exactly this reason.

**A shared mikey key collapses ownership.** A group key (`crv-group`) or the
collaboration-wide `mu2e` key makes every holder the *same* owner — they see,
overwrite, and retire each other's documents, with no way to tell who did
what. That is intended for now (shared group memory is a plausible feature),
but it is worth knowing before handing out a shared key to people who expect
private notes. Flagged for a future revisit.

## Tools

| Tool | Purpose |
|---|---|
| `get_server_info()` | version, config, and the owner your key resolves to |
| `list_projects()` | your project names — cheap discovery entry point |
| `list_documents(...)` | browse: metadata **and size**, never content |
| `list_versions(project, name)` | every stored version, newest first |
| `get_document(project, name, version=, file_path=)` | fetch the text |
| `put_document(project, name, content, mode=, ...)` | create or add a version |
| `set_metadata(project, name, ...)` | metadata-only update |

`list_documents` filters on project, name substring, keywords, description
substring, minimum weight, creation/update time, and whether to include
expired or retired documents; it sorts by weight (default), name, created,
updated, or size.

### Size discipline

`list_documents` returns `size_bytes` and never returns content, so an agent
can decide what is worth reading before reading it. `get_document` then
refuses to return more than `max_inline_bytes` (default 100 000) inline,
erroring with the actual size instead — so a large document cannot land in a
caller's context unintentionally. Same reasoning as `runs-mcp`'s
`get_config_blob`. Pass `file_path` to write the content to disk instead.

`file_path` writes on the **server host**, as the account running the service.
That is only useful if the caller can read that path — fine for a client on
the mu2e cluster with shared storage mounted, useless otherwise. The agent
chooses the path (it knows whether it has a working area); the server will not
create directory trees and will not overwrite an existing file unless
`overwrite=true`.

### Versioning

`put_document` on an existing `(project, name)` creates a new version. Old
versions stay retrievable via `get_document(version=N)` and are never
modified. `mode="replace"` stores what you pass; `mode="append"` concatenates
onto the current content (inserting a newline if needed) and stores the result
as a complete new version — snapshots, not deltas, so any version is a single
row read.

Documents are capped at 1 MiB (`--max-content-bytes`). Markdown by convention;
any text is accepted.

### Metadata

All optional — keywords, description, weight (0–100), expiry — but worth
providing, since it is what makes a document findable later. Updates **merge**:
fields you omit are carried forward, not blanked. Pass an empty string to
clear a field explicitly. Previous metadata is archived, not overwritten.

`expires` is advisory: it filters listings, it does not remove anything.

## Nothing is ever deleted

The server cannot delete. `set_metadata(retired=true)` hides a document from
listings, which is the closest an agent can get. Actually destroying content —
a credential pasted into a document, personal data, a mistake that must not
persist — requires a database administrator; the exact statements are at the
bottom of `sql/create_tables.sql`.

## Database

`mu2e_ai_prd` on `ifdb11:5477`, schema `memory`. **Kerberos-authenticated from
the ambient environment — there are no database credentials in any config file
or environment variable.**

Privileges are split deliberately. The server logs in as `mu2eai`, which holds
`SELECT` directly, so **reads run unelevated**. **Writes** run inside a
transaction under `SET LOCAL ROLE update_role` — `LOCAL`, so the elevation
reverts on commit or rollback and cannot leak to whichever request picks up
that pooled connection next.

The point of the split is that an accidental `INSERT` from a code path meant
only to read is refused by the database instead of silently succeeding — worth
having in a store where a stray write is permanent and cannot be deleted. It
guards against a coding mistake, not a determined attacker: `put_document`
legitimately elevates, so anything able to reach the write path can elevate
too.

`--check` verifies both paths independently, and says which grant is missing
if either fails.

The schema must exist before the server will start. It is **not** created
automatically — the MCP's role cannot create tables, and schema changes should
be a considered act:

```bash
psql -h ifdb11 -p 5477 mu2e_ai_prd -f sql/create_tables.sql
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MIKEY_KEYS_FILE` | _(required)_ | mikey keys file — **server will not start without it** |
| `MEMORY_MCP_HOST` | `0.0.0.0` | bind host |
| `MEMORY_MCP_PORT` | `8007` | bind port |
| `MEMORY_MCP_DB_HOST` | `ifdb11` | Postgres host |
| `MEMORY_MCP_DB_PORT` | `5477` | Postgres port |
| `MEMORY_MCP_DB_NAME` | `mu2e_ai_prd` | database |
| `MEMORY_MCP_DB_SCHEMA` | `memory` | schema |
| `MEMORY_MCP_WRITE_ROLE` | `update_role` | role assumed for inserts |
| `MEMORY_MCP_MAX_CONTENT_BYTES` | `1048576` | largest accepted document |
| `MEMORY_MCP_DB_POOL_MAX` | `4` | connection pool size |
| `MEMORY_MCP_LOG_LEVEL` | `INFO` | logger level |
| `KRB5CCNAME` | _(system default)_ | only if the credential cache is non-default |

Most have matching CLI flags (`--db-host`, `--write-role`, …); the flag wins.

## `--check`

Verifies configuration without binding a socket: confirms `MIKEY_KEYS_FILE` is
set, opens a real database connection, confirms the schema is present, and
confirms the write role can actually be assumed (inside a transaction it rolls
back, so `--check` never writes). Fails with a one-line message, exit 1.

## Local dev

```bash
mu2einit
slc uv
cd aitools/mcp/memory
uv venv
uv pip install -e .
export MIKEY_KEYS_FILE=/path/to/keys
.venv/bin/memory-mcp.sh --check
.venv/bin/memory-mcp.sh --port=8007
```

Smoke test (read-only by default — `--write` creates **permanent** rows under
project `_smoketest`, since nothing can be deleted afterwards):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8007 mikey_<token>
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8007 mikey_<token> --write
```

## Server-account install

```bash
cd aitools/mcp/memory
./scripts/install.sh /path/to/deploy/memory v0.7.0
# first install only, as a user with DDL rights:
psql -h ifdb11 -p 5477 mu2e_ai_prd -f /path/to/deploy/memory/current/.venv/share/memory-mcp/create_tables.sql
MIKEY_KEYS_FILE=/path/to/keys /path/to/deploy/memory/current/.venv/bin/memory-mcp.sh --check
/path/to/deploy/memory/current/.venv/bin/memory-mcp-install-unit.sh \
  --port 8007 --mikey-keys-file /path/to/keys
```

`--mikey-keys-file` is required. If the Kerberos credential cache is in a
non-default location, pass `--env-file` with `KRB5CCNAME=...` — as plain
`KEY=VALUE` lines, **not** `export KEY=VALUE`, which systemd does not
understand (it is not a shell, and the `export` form silently fails to set the
variable).

## Client config

```bash
claude mcp add --transport http --scope user memory http://<host>:8007/mcp \
  --header "Authorization: Bearer <mikey-token>"
```

`--scope user` or `--scope local` — never `--scope project` (see mikey's
README). Note that the token determines which documents you see, so
registering the same server twice with two different tokens gives you two
independent memory namespaces side by side.

## Noted for future work

- **Semantic search.** Retrieval today is exact/substring/keyword matching
  only. RAG-style embedding search would fit the use case well, but no MCP in
  this workspace makes LLM calls at present; deferred deliberately.
- **Human access.** The prompt anticipated people reading these documents too.
  Everything is owner-scoped via bearer token, so a browser-facing `/list`
  page (like `registry`'s) needs an authentication story first. Agents only,
  for now.
- **Shared-key ownership**, as described above.
