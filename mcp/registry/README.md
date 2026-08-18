# registry-mcp (prototype)

Trivial streamable-HTTP MCP server with a live MCP-registry endpoint. This
is the proof of principle for the new MCP pattern in this workspace: a
`pyproject.toml`-based package, installed with plain `uv` primitives
(`uv venv` + `uv pip install`, via git subdirectory syntax) into a visible,
versioned directory in a service account, run persistently under
`systemd --user`, and reachable over HTTP instead of spawned per-connection
over stdio like the older MCPs in this repo (`../metacat`, `../sim-epochs`,
`../dqm`). Originally prototyped under the name `hello`; renamed to
`registry` once the registry/discovery angle became the actual point of
this server rather than just a bonus feature of a "hello world" example.

## Exposed MCP tools

- `say_hello(name="world")` -- trivial greeting, proves the server is alive
  and reachable end to end.
- `list_mcp_servers()` -- returns the current MCP server registry (name ->
  url, description) as JSON text, read from the `--registry` file. The
  description travels with the URL specifically so an agent deciding which
  server to use has enough context to judge, not just an address to dial.

## Plain HTTP endpoints

- `GET /registry` -- same registry data as `list_mcp_servers` (url +
  description per server), but outside the MCP protocol, so a plain
  `curl`/script can fetch it to bootstrap a client's static `mcpServers`
  config without needing an MCP client library.
- `GET /list` -- the same data as an HTML table, for humans browsing to the
  server directly.

This does **not** let an MCP client dynamically add live tool connections to
whatever's listed in the registry -- mainstream MCP hosts (Claude Code,
Claude Desktop, Cline, ...) read `mcpServers` from a static config file at
startup and open one session per entry. `/registry` exists to make
generating/updating that static file a one-line `curl` instead of hand-
editing ports as more HTTP MCPs come online. (A true single-entry-point
gateway that fans live tool calls out to multiple backend MCP servers is a
different, heavier component -- worth revisiting once there's more than one
real HTTP backend to test it against.)

## How it's installed and started -- no generated files

`uv pip install` on this package produces two files in the target venv's
`bin/`, installed together by the same command:

- `registry-mcp` -- the Python entry point (`[project.scripts]`)
- `registry-mcp.sh` -- a plain bash wrapper (`[tool.setuptools] script-files`),
  installed as a sibling of `registry-mcp` in the same directory. It's the
  place for any server-side environment setup a real MCP might need (e.g.
  `spack load ...`) before it `exec`s its sibling `registry-mcp`.
  registry-mcp itself needs no such setup, so the wrapper is currently a
  no-op passthrough.

That's the entire install -- no source tree gets copied, no config file
gets generated, nothing else needs to happen. All runtime configuration
(bind host/port, which registry file to read) is passed as CLI args at
start time, so the systemd unit is a single self-contained `ExecStart` line:

```
ExecStart=<deploy-root>/current/.venv/bin/registry-mcp.sh --port=8000 --registry=/abs/path/ports.json
```

Versioned rollback still works exactly as you'd expect: each install lives
under `<deploy-root>/releases/<ref>/.venv`, and `<deploy-root>/current` is a
symlink to whichever release is active. Rolling back is repointing that
symlink (or re-running `install.sh` with an older ref) and restarting the
unit.

## Registry file (ports.json)

A name -> `{port, description}` map, e.g.:

```json
{
  "registry": {
    "port": 8000,
    "description": "MCP server registry: list_mcp_servers tool, GET /registry (JSON), GET /list (HTML)."
  }
}
```

The description lives here, alongside the port, rather than in a separate
file -- one operator-edited fact sheet per deployment, and it flows through
unchanged to the tool call, `/registry`, and `/list` (all three are built
from the same `_load_entries()`/`_build_registry()` pair in `server.py`, so
there's no separate place for the three views to drift apart).

This is genuinely separate from the versioned install above: it's operator-
edited state that must survive upgrades untouched, so it's never generated
or copied by `install.sh` -- only referenced, by path, via `--registry`.
`config/ports.json` in this checkout is a ready-to-use copy; point
`--registry` at it directly, or at your own copy anywhere else.

Port convention for this workspace: `registry` on 8000, other HTTP MCPs on
8001-8009 as they're migrated from stdio.

## Local dev run

On mu2e machines, get `uv` from the cluster's spack packages first:

```bash
mu2einit
slc uv
```

Then:

```bash
cd aitools/mcp/registry
uv venv
uv pip install -e .
.venv/bin/registry-mcp.sh --port=8000 --registry=config/ports.json
```

Startup compatibility check (imports/constructs only, no bind/listen):

```bash
.venv/bin/registry-mcp.sh --check
```

Smoke test against a running server (MCP handshake + tool calls + `/registry` + `/list`):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8000
```

## Server-account install (uv, git subdirectory syntax)

Run in the account that will host the service (verified with
`scripts/check_systemd_user.sh` -- see below). Requires a checkout of this
repo on the server (for `install.sh` and `config/ports.json`) plus `uv` on
`PATH`; the actual Python package install is pinned independently via `uv`
against a git ref, not copied from the local checkout:

```bash
cd aitools/mcp/registry
./scripts/install.sh /path/to/deploy/registry v0.1.0
```

This creates:

- `/path/to/deploy/registry/releases/v0.1.0/.venv` (via `uv venv` + `uv pip
  install "registry-mcp @ git+https://github.com/Mu2e/aitools@v0.1.0#subdirectory=aitools/mcp/registry"`)
- `/path/to/deploy/registry/current` (symlink to the release above)
- `/path/to/deploy/registry/registry-mcp.service` (rendered systemd unit,
  real paths and `--registry` filled in)

Then, as printed by `install.sh`:

```bash
/path/to/deploy/registry/current/.venv/bin/registry-mcp.sh --check
mkdir -p ~/.config/systemd/user
cp /path/to/deploy/registry/registry-mcp.service ~/.config/systemd/user/registry-mcp.service
systemctl --user daemon-reload
systemctl --user enable --now registry-mcp
systemctl --user status registry-mcp
```

Requires linger enabled once per account so the service survives logout
(permanent, survives reboot):

```bash
loginctl enable-linger        # or: sudo loginctl enable-linger <account>
```

Verify systemd `--user` access before relying on any of this:

```bash
./scripts/check_systemd_user.sh
```

## Client config

```json
{
  "mcpServers": {
    "registry": { "url": "http://<host>:8000/mcp" }
  }
}
```

## Notes

- Binds `0.0.0.0:8000` by default -- reachable across the org network;
  outside traffic is blocked by firewall. No auth is implemented yet.
- CLI flags (`--host`, `--port`, `--registry`, `--public-host`) are primary;
  `REGISTRY_MCP_HOST` / `REGISTRY_MCP_PORT` / `REGISTRY_MCP_REGISTRY_FILE` /
  `REGISTRY_MCP_PUBLIC_HOST` env vars are used only as defaults when a flag
  isn't given.
- `--public-host` overrides the hostname advertised in registry URLs
  (defaults to the host's FQDN); useful if the bind host and the externally
  reachable name differ.
- Whether the smoke test should also serve as the systemd health check is
  still an open question -- deferred for now.
- Verified locally end to end under this name (`uv venv` + `uv pip install
  -e .`, `--check`, a live server hit via `curl /registry` and `/list`, and
  a full MCP handshake via `smoke_test_http.py`) using the cluster's `uv`
  (`mu2einit && slc uv`). Not yet verified: an actual non-editable install
  from the git subdirectory URL (`scripts/install.sh` against a pushed
  ref), and the real server account's systemd unit.
- `/list` builds its own HTML inline (an f-string in `server.py`); no
  templating engine and no new dependency. `html.escape()` is applied to
  every value even though `ports.json` is operator-edited/trusted, as cheap
  insurance rather than a response to any actual threat.
- The `mcp` package went through a major API change at PyPI version 2.0.0
  (released after this pattern was first drafted): `FastMCP` was renamed to
  `MCPServer` (now at `mcp.server.mcpserver.MCPServer`), the constructor no
  longer takes `host`/`port`, and `.settings` doesn't carry them either --
  bind host/port are passed directly to `run(transport=..., host=..., port=...)`.
  `pyproject.toml` pins `mcp>=2.0.0` accordingly; pin more precisely if a
  future `mcp` release changes this API again.
