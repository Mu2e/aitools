# metacat-mcp

Read-only streamable-HTTP MCP server for Mu2e metacat discovery using the
`metacat-client` Python API. Converted from the original stdio-transport,
spack-dependent server to the uv-installed, systemd `--user`-run HTTP
pattern proven out by `../registry` and `../dqm`: a `pyproject.toml`-based
package, installed with plain `uv` primitives (`uv venv` + `uv pip install`,
via git subdirectory syntax) into a visible, versioned directory in a
service account, run persistently under `systemd --user`, and reachable
over HTTP instead of spawned per-connection over stdio. This conversion
also drops the spack/cvmfs dependency entirely: `metacat.webapi` now comes
from the `metacat-client` package on PyPI (the same client-side code that
ships from the `fermitools/metacat` repo, version-aligned with the spack
`metacat@4.1.3+client_only` build), pulled in as a normal pinned dependency
instead of being reached via `setupmu2e-art.sh` + `muse setup ops` path
manipulation. None of the discovery/query logic changed.

## Scope

- Read-only operations only
- No explicit auth/token handling in server code -- `metacat.webapi.MetaCatClient()`
  is configured entirely via the `METACAT_SERVER_URL` / `METACAT_AUTH_SERVER_URL`
  environment variables
- Streamable-HTTP transport
- No write tools exposed

## Exposed MCP tools

- `discover_datasets(...)`
  - explicit filters for namespace, name wildcard, created date range, non-empty/counts, pagination
- `get_dataset_details(dataset_did, include_sample_file, include_sample_metadata)`
  - dataset info + optional sample file and sample metadata keys
- `query_dataset_files(...)`
  - common file filters: created date, size, n_events, run/subrun ranges, sorting, pagination
- `get_server_info()`
  - capabilities and safety notes

## How it's installed and started

A real (non-editable) `uv pip install` on this package produces, in the
target venv:

- `bin/metacat-mcp` -- the Python entry point (`[project.scripts]`)
- `bin/metacat-mcp.sh` -- a plain bash wrapper (`[tool.setuptools]
  script-files`), installed as a sibling of `metacat-mcp` in the same
  directory. It's the place for any server-side environment setup a real
  MCP might need (e.g. `spack load ...`) before it `exec`s its sibling
  `metacat-mcp`. metacat-mcp itself needs no such setup -- `metacat-client`
  is a pinned dependency of this venv and talks to the MetaCat server over
  plain HTTPS with no mu2e/cvmfs offline-software environment -- so the
  wrapper is currently a no-op passthrough, same as registry-mcp.sh and
  dqm-mcp.sh. (The previous stdio version of this server sourced
  `setupmu2e-art.sh` + `muse setup ops` and merged the resulting
  `PYTHONPATH` with the venv's, to reach a spack-built `metacat` client;
  that whole mechanism is gone now that `metacat-client` ships on PyPI.)
- `bin/metacat-mcp-install-unit.sh` -- renders and registers the systemd
  `--user` unit; see below.

No source tree gets copied, and nothing here needs a checkout to work
except `install.sh` itself (a convenience wrapper, not a requirement -- see
its own usage text for the fully manual 3-command alternative). All runtime
configuration (bind host/port) is passed as CLI args at start time, and the
MetaCat server endpoint comes from `METACAT_SERVER_URL` /
`METACAT_AUTH_SERVER_URL`, so the systemd unit's `ExecStart` is a single
self-contained line pointing at `metacat-mcp.sh`, resolved relative to
wherever that script actually lives -- see the systemd section below for
exactly how that line gets generated.

Versioned rollback still works exactly as you'd expect: each install lives
under `<deploy-root>/releases/<ref>/.venv`, and `<deploy-root>/current` is a
symlink to whichever release is active. Rolling back is repointing that
symlink (or re-running `install.sh` with an older ref) and restarting the
unit.

## Local dev run

On mu2e machines, get `uv` from the cluster's spack packages first:

```bash
mu2einit
slc uv
```

Then:

```bash
cd aitools/mcp/metacat
uv venv
uv pip install -e .
.venv/bin/metacat-mcp --host=127.0.0.1 --port=8002
```

(`.venv/bin/metacat-mcp.sh` also works -- it's the same no-op-setup wrapper
the systemd unit calls, so exercising it locally covers the same code path.)

Startup compatibility check (imports/constructs only, no bind/listen):

```bash
.venv/bin/metacat-mcp.sh --check
```

Smoke test against a running server (MCP handshake + tool calls):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8002
```

## Config (CLI flags / env vars)

CLI flags are primary; the corresponding env var is used only as a default
when a flag isn't given:

| Flag | Env var | Default |
|---|---|---|
| `--host` | `METACAT_MCP_HOST` | `0.0.0.0` |
| `--port` | `METACAT_MCP_PORT` | `8002` |

The MetaCat server endpoint is baked in as a default -- Mu2e's production
instance has been stable for years -- and only needs overriding for a
non-default deployment (e.g. pointing at a dev/test MetaCat instance):

| Env var | Purpose | Default |
|---|---|---|
| `METACAT_SERVER_URL` | MetaCat server endpoint | `https://metacat.fnal.gov:9443/mu2e_meta_prod/app` |
| `METACAT_AUTH_SERVER_URL` | MetaCat auth server endpoint | `https://metacat.fnal.gov:8143/auth/mu2e` |

These aren't server CLI flags -- `_client()` reads the env var (falling back
to the hardcoded default above) and passes it explicitly to
`MetaCatClient(server_url=..., auth_server_url=...)` on every call. Check
`get_server_info()`'s `metacat` field to see which endpoint a running
deployment actually resolved.

`METACAT_MCP_LOG_LEVEL` (default `INFO`) controls log verbosity.

## Server-account install

No checkout is actually required for the package itself -- `uv venv` +
`uv pip install "metacat-mcp @ git+...#subdirectory=mcp/metacat"` is the
whole install, straight from GitHub. A checkout is only useful for the
`install.sh` convenience wrapper; see `install.sh --help` for the fully
manual equivalent if you'd rather skip it.

```bash
cd aitools/mcp/metacat
./scripts/install.sh /path/to/deploy/metacat v0.2.0
```

This creates `/path/to/deploy/metacat/releases/v0.2.0/.venv` and symlinks
`/path/to/deploy/metacat/current` to it. It does not touch systemd --
`install.sh` prints the next-step command for that, which is just:

```bash
/path/to/deploy/metacat/current/.venv/bin/metacat-mcp.sh --check
/path/to/deploy/metacat/current/.venv/bin/metacat-mcp-install-unit.sh --port 8002
```

`metacat-mcp-install-unit.sh` renders the unit into *this release's own*
`share/metacat-mcp/metacat-mcp.service` (not `~/.config`) and registers it
with `systemctl --user link --force`, which creates a symlink in
`~/.config/systemd/user/` pointing back at that file -- so the real unit
content stays with the installed code, `~/.config` only ever holds a
pointer, and there's nothing to hand-edit or copy. It then runs
`daemon-reload` and `enable --now` (pass `--no-enable` to render + link
without starting anything). Pass `--metacat-server-url` /
`--metacat-auth-server-url` if this deploy needs a non-default MetaCat
endpoint -- they're written into the unit as `Environment=` lines. Safe to
re-run any time (e.g. after changing `--port`, or after a redeploy) --
`--force` makes the re-link a no-op other than picking up the new
`ExecStart`/`Environment` lines.

Requires linger enabled once per account so the service survives logout
(permanent, survives reboot):

```bash
loginctl enable-linger        # or: sudo loginctl enable-linger <account>
```

See `../registry/scripts/check_systemd_user.sh` to verify systemd `--user`
access before relying on any of this (a general check, not metacat-specific).

## Client config

```json
{
  "mcpServers": {
    "metacat": { "url": "http://<host>:8002/mcp" }
  }
}
```

Once deployed and reachable, register this URL/port in
`../registry/config/ports.json` on the registry server so it also shows up
via `list_mcp_servers`/`GET /registry`/`GET /list`.

## Notes

- Binds `0.0.0.0:8002` by default -- reachable across the org network;
  outside traffic is blocked by firewall. No auth is implemented yet.
- Port convention for this workspace: `registry` on 8000, other HTTP MCPs on
  8001-8009 as they're migrated from stdio; `dqm` is 8001, `metacat` is 8002.
- `discover_datasets(..., with count filters)` may be slower on broad scope.
- Prefer narrowing by namespace + name pattern + date window first.
- This server intentionally does not expose write tools.
- Uses `mcp>=2.0.0` (`FastMCP` renamed to `MCPServer`, host/port moved from
  the constructor to `run()`) -- see `../registry/README.md`'s Notes section
  for the full API-change writeup, which applies identically here.
- `metacat-client` is pinned to `>=4.1.3` to track the version currently
  deployed via spack (`metacat@4.1.3+client_only`); bump deliberately, not
  incidentally, since MetaCat server/client protocol compatibility matters.
