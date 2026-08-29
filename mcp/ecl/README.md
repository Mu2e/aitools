# ecl-mcp

Streamable-HTTP MCP server for the Fermilab Electronic Collaboration
Logbook (ECL). Read-only by default. The first server in this workspace to
be deployed with [mikey](../mikey) bearer-token auth from the start,
rather than added later -- see `AUTHPLAN.md` at the repo root.

## Relationship to ecl-api

The ECL protocol logic (signature scheme, active-server redirect
resolution, XML parsing, category/tag/form sampling) is reused as-is from
[Mu2e/ecl-api](https://github.com/Mu2e/ecl-api) (MIT, a repo Mu2e already
owns) via a normal git dependency -- not vendored/copied.

ecl-api also ships its own bundled MCP server, which this package does
**not** reuse: it targets `mcp.server.fastmcp.FastMCP`, the pre-2.0 API,
which `mcp>=2.0.0` (pinned here, same as every other server in this
workspace) removes outright -- `ModuleNotFoundError`, confirmed directly,
not assumed. It also has none of this workspace's operational conventions
(no `--check`, no systemd unit, no install.sh, no mikey hook). The tool
logic itself ported over almost line-for-line onto `MCPServer` -- this
package's `server.py` is a flat, single-file port of ecl-api's
`mcp/tools/*.py`, matching `dqm-mcp`'s style rather than ecl-api's own
`tools/` subpackage split (not enough tools here to earn that indirection).

## Exposed tools

Read-only (always registered): `get_server_info`, `ecl_search_entries`,
`ecl_search_entry_ids`, `ecl_get_entry`, `ecl_list_categories`,
`ecl_list_tags`, `ecl_list_forms`.

`ecl_post_entry` is additionally registered when `ECL_MCP_READ_ONLY=false`.
It takes `do_post` (default `False`) so an agent can prepare and inspect an
entry before committing it -- there is no undo once `do_post=True` is used.

## Environment variables

| Variable             | Default       | Purpose                                        |
|----------------------|---------------|-------------------------------------------------|
| `ECL_URL`            | _(required)_  | ECL base URL, e.g. `https://dbweb9.fnal.gov:8443/ECL/mu2e/E` |
| `ECL_USER_NAME`      | _(required)_  | XML user name                                  |
| `ECL_PASSWORD`       | _(required)_  | XML user password                              |
| `ECL_MCP_READ_ONLY`  | `true`        | Set to `false` to register `ecl_post_entry`    |
| `ECL_MCP_HOST`       | `0.0.0.0`     | HTTP bind host                                 |
| `ECL_MCP_PORT`       | `8005`        | HTTP bind port                                 |
| `ECL_MCP_LOG_LEVEL`  | `INFO`        | Logger level                                   |
| `MIKEY_KEYS_FILE`    | _(unset)_     | Path to mikey's shared keys file -- unset means auth stays off |
| `MIKEY_SERVER_URL`   | _(unset)_     | See mikey's README -- placeholder OAuth field, rarely needs setting |

None of these are CLI flags -- `ECL_URL`/`ECL_USER_NAME`/`ECL_PASSWORD` are
credentials and `MIKEY_KEYS_FILE` gates auth, so all of them stay out of
`ps` output and out of the rendered systemd unit's `ExecStart` line (see
`ecl-mcp-install-unit.sh --env-file`).

## `--check`

Unlike `dqm-mcp`'s/`registry-mcp`'s `--check`, this one makes a real
network call: constructing the ECL client resolves the active server via a
redirect-following GET. It fails clearly (exit 1) if credentials are
unset, and also fails if the server can't actually be resolved --
`ecl-api`'s own `_resolve_server` swallows connection errors internally
(prints a message, returns `None`) rather than raising, so this wraps that
with an explicit check rather than reporting `OK` on an unreachable
`ECL_URL`.

## Local dev

```bash
mu2einit
slc uv
cd aitools/mcp/ecl
uv venv
uv pip install -e .
export ECL_URL=... ECL_USER_NAME=... ECL_PASSWORD=...
.venv/bin/ecl-mcp.sh --check
.venv/bin/ecl-mcp.sh --port=8005
```

Smoke test against a running server (pass a mikey token as the second
argument if auth is enabled; omit it only if `MIKEY_KEYS_FILE` is unset):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8005 mikey_<token>
```

## Server-account install

```bash
cd aitools/mcp/ecl
./scripts/install.sh /path/to/deploy/ecl v0.1.0
```

Then create an env file (KEY=VALUE lines: `ECL_URL`, `ECL_USER_NAME`,
`ECL_PASSWORD`, `MIKEY_KEYS_FILE`, optionally `ECL_MCP_READ_ONLY`), set it
`0600`, and:

```bash
/path/to/deploy/ecl/current/.venv/bin/ecl-mcp-install-unit.sh \
  --port 8005 --env-file /path/to/ecl-mcp.env
```

See `scripts/ecl-mcp-install-unit.sh --help` for why credentials go through
`--env-file` (rendered as the unit's `EnvironmentFile=`) rather than as
flags -- flags would show up in `ps` and get baked in plaintext into the
unit file itself.

## Client config

```bash
claude mcp add --transport http --scope user ecl http://<host>:8005/mcp \
  --header "Authorization: Bearer <mikey-token>"
```

`--scope user` or `--scope local` -- never `--scope project` (see mikey's
README for why).
