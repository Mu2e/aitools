# dqm-mcp (prototype)

Read-only streamable-HTTP MCP server for Mu2e DQM metrics using Query Engine
HTTP calls. Converted from the original stdio-transport server to the
uv-installed, systemd `--user`-run HTTP pattern proven out by `../registry`:
a `pyproject.toml`-based package, installed with plain `uv` primitives (`uv
venv` + `uv pip install`, via git subdirectory syntax) into a visible,
versioned directory in a service account, run persistently under `systemd
--user`, and reachable over HTTP instead of spawned per-connection over
stdio. This conversion only changes transport/install/run mechanics --
none of the DQM query logic changed.

## Scope

- Read-only operations only
- Fixed dbname default: `mu2e_dqm_prd`
- Fixed endpoint default: QE nocache URL (`:9443`)
- Streamable-HTTP transport
- JSON table responses intended for client-side LLM interpretation

## Exposed MCP tools

- `get_server_info()`
  - server defaults, QE endpoint/dbname, limits
- `list_sources(...)`
  - list source tuples from `dqm.sources`
- `list_versions(...)`
  - list available source versions
- `list_values(...)`
  - list metric names from `dqm.values`
- `list_intervals(...)`
  - query/sort intervals by run/subrun or time
- `query_metrics(...)`
  - query `dqm.numbers` or `dqm.limits` with source/value/interval filters
  - defaults: `recent_days=10`, `limit=100`
  - supports expanded source/value/interval payloads

## How it's installed and started

A real (non-editable) `uv pip install` on this package produces, in the
target venv:

- `bin/dqm-mcp` -- the Python entry point (`[project.scripts]`)
- `bin/dqm-mcp.sh` -- a plain bash wrapper (`[tool.setuptools]
  script-files`), installed as a sibling of `dqm-mcp` in the same
  directory. It's the place for any server-side environment setup a real
  MCP might need (e.g. `spack load ...`) before it `exec`s its sibling
  `dqm-mcp`. dqm-mcp itself needs no such setup -- it's a plain HTTP client
  (stdlib + `requests`) reaching the Query Engine's public HTTPS endpoint
  with no credentials, no mu2e/cvmfs offline-software environment, and no
  mu2e-specific imports -- so the wrapper is currently a no-op passthrough,
  same as registry-mcp.sh. (An earlier version of this wrapper sourced
  `setupmu2e-art.sh` + `muse setup ops`, carried over unexamined from the
  old stdio launcher; removed after confirming the QE endpoint is reachable
  in a fully stripped environment -- `env -i`, no Kerberos ticket -- and
  that `muse setup ops`'s exported `PYTHONPATH` was actually shadowing this
  venv's pinned `mcp>=2.0.0` deps with older cvmfs/spack copies.)
- `bin/dqm-mcp-install-unit.sh` -- renders and registers the systemd
  `--user` unit; see below.

No source tree gets copied, and nothing here needs a checkout to work
except `install.sh` itself (a convenience wrapper, not a requirement -- see
its own usage text for the fully manual 3-command alternative). All runtime
configuration (bind host/port, QE base URL/dbname/timeout) is passed as CLI
args at start time, so the systemd unit's `ExecStart` is a single
self-contained line pointing at `dqm-mcp.sh`, resolved relative to wherever
that script actually lives -- see the systemd section below for exactly how
that line gets generated.

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
cd aitools/mcp/dqm
uv venv
uv pip install -e .
.venv/bin/dqm-mcp --host=127.0.0.1 --port=8001
```

(`.venv/bin/dqm-mcp.sh` also works -- it's the same no-op-setup wrapper the
systemd unit calls, so exercising it locally covers the same code path.)

Startup compatibility check (imports/constructs only, no bind/listen):

```bash
.venv/bin/dqm-mcp.sh --check
```

Smoke test against a running server (MCP handshake + tool calls):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8001
```

## Config (CLI flags / env vars)

CLI flags are primary; the corresponding env var is used only as a default
when a flag isn't given:

| Flag | Env var | Default |
|---|---|---|
| `--host` | `DQM_MCP_HOST` | `0.0.0.0` |
| `--port` | `DQM_MCP_PORT` | `8001` |
| `--qe-base-url` | `DQM_QE_BASE_URL` | `https://dbdata0vm.fnal.gov:9443/QE/mu2e/prod/app/SQ/query?` |
| `--qe-dbname` | `DQM_QE_DBNAME` | `mu2e_dqm_prd` |
| `--qe-timeout-seconds` | `DQM_QE_TIMEOUT_SECONDS` | `30` |

`--qe-base-url` must point at the nocache endpoint (`:9443`); a `:8444/`
cache-endpoint URL is rejected at startup.

`DQM_MCP_LOG_LEVEL` (default `INFO`) still controls log verbosity, same as
before this conversion.

## Server-account install

No checkout is actually required for the package itself -- `uv venv` +
`uv pip install "dqm-mcp @ git+...#subdirectory=mcp/dqm"` is the whole
install, straight from GitHub. A checkout is only useful for the
`install.sh` convenience wrapper; see `install.sh --help` for the fully
manual equivalent if you'd rather skip it.

```bash
cd aitools/mcp/dqm
./scripts/install.sh /path/to/deploy/dqm v0.2.0
```

This creates `/path/to/deploy/dqm/releases/v0.2.0/.venv` and symlinks
`/path/to/deploy/dqm/current` to it. It does not touch systemd --
`install.sh` prints the next-step command for that, which is just:

```bash
/path/to/deploy/dqm/current/.venv/bin/dqm-mcp.sh --check
/path/to/deploy/dqm/current/.venv/bin/dqm-mcp-install-unit.sh --port 8001
```

`dqm-mcp-install-unit.sh` renders the unit into *this release's own*
`share/dqm-mcp/dqm-mcp.service` (not `~/.config`) and registers it with
`systemctl --user link --force`, which creates a symlink in
`~/.config/systemd/user/` pointing back at that file -- so the real unit
content stays with the installed code, `~/.config` only ever holds a
pointer, and there's nothing to hand-edit or copy. It then runs
`daemon-reload` and `enable --now` (pass `--no-enable` to render + link
without starting anything). Safe to re-run any time (e.g. after changing
`--port`, or after a redeploy) -- `--force` makes the re-link a no-op other
than picking up the new `ExecStart`.

Requires linger enabled once per account so the service survives logout
(permanent, survives reboot):

```bash
loginctl enable-linger        # or: sudo loginctl enable-linger <account>
```

See `../registry/scripts/check_systemd_user.sh` to verify systemd `--user`
access before relying on any of this (a general check, not dqm-specific).

## Client config

```json
{
  "mcpServers": {
    "dqm": { "url": "http://<host>:8001/mcp" }
  }
}
```

Once deployed and reachable, register this URL/port in
`../registry/config/ports.json` on the registry server so it also shows up
via `list_mcp_servers`/`GET /registry`/`GET /list`.

## Notes

- Binds `0.0.0.0:8001` by default -- reachable across the org network;
  outside traffic is blocked by firewall. No auth is implemented yet.
- Port convention for this workspace: `registry` on 8000, other HTTP MCPs on
  8001-8009 as they're migrated from stdio; `dqm` takes 8001.
- All database reads are over Query Engine HTTP.
- Default behavior is nocache endpoint use.
- Query defaults are intentionally conservative (`limit=100`, recent window
  10 days).
- If filters are selective, increase `scan_limit` in `query_metrics(...)`.
- Uses `mcp>=2.0.0` (`FastMCP` renamed to `MCPServer`, host/port moved from
  the constructor to `run()`) -- see `../registry/README.md`'s Notes section
  for the full API-change writeup, which applies identically here.
