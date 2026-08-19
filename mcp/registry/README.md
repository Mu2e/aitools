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

## How it's installed and started

A real (non-editable) `uv pip install` on this package produces, in the
target venv:

- `bin/registry-mcp` -- the Python entry point (`[project.scripts]`)
- `bin/registry-mcp.sh` -- a plain bash wrapper (`[tool.setuptools]
  script-files`), installed as a sibling of `registry-mcp` in the same
  directory. It's the place for any server-side environment setup a real
  MCP might need (e.g. `spack load ...`) before it `exec`s its sibling
  `registry-mcp`. registry-mcp itself needs no such setup, so the wrapper
  is currently a no-op passthrough.
- `bin/registry-mcp-install-unit.sh` -- renders and registers the systemd
  `--user` unit; see below.
- `share/registry-mcp/ports.json` -- the `config/ports.json` template from
  this checkout (`[tool.setuptools] data-files`), shipped so a fresh install
  has *something* to look at without needing a checkout. It's a template,
  not live config -- see "Registry file" below.

No source tree gets copied, and nothing here needs a checkout to work
except `install.sh` itself (a convenience wrapper, not a requirement -- see
its own usage text for the fully manual 3-command alternative). All runtime
configuration (bind host/port, which registry file to read) is passed as
CLI args at start time, so the systemd unit's `ExecStart` is a single
self-contained line pointing at `registry-mcp.sh`, resolved relative to
wherever that script actually lives -- see the systemd section below for
exactly how that line gets generated.

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
edited state that must survive upgrades untouched. Nothing in the install
generates or edits it for you -- `--registry` always just points at a path
you own. Two starting points for that path, both meant to be copied out and
edited, never used in place long-term:

- `share/registry-mcp/ports.json` in the installed venv (shipped via
  `uv pip install`, see above) -- also what `--registry` defaults to when
  omitted, so `registry-mcp.sh --check` and a first smoke test work with no
  setup at all.
- `config/ports.json` in this checkout, for local dev.

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

Note: `uv pip install -e .` (editable) does not process `data-files` -- this
is a general setuptools/editable-install limitation, not uv-specific -- so a
local `-e .` dev venv has no `share/registry-mcp/ports.json`; the default
falls back to `config/ports.json` in the checkout instead (same effect,
different path). Real deployments never use `-e .`, so they always get the
`share/` copy.

## Server-account install

No checkout is actually required for the package itself -- `uv venv` +
`uv pip install "registry-mcp @ git+...#subdirectory=mcp/registry"` is the
whole install, straight from GitHub. A checkout is only useful for the
`install.sh` convenience wrapper and `check_systemd_user.sh`; see
`install.sh --help` for the fully manual equivalent if you'd rather skip it.

```bash
cd aitools/mcp/registry
./scripts/install.sh /path/to/deploy/registry v0.1.0
```

This creates `/path/to/deploy/registry/releases/v0.1.0/.venv` and symlinks
`/path/to/deploy/registry/current` to it. It does not touch systemd --
`install.sh` prints the next-step command for that, which is just:

```bash
/path/to/deploy/registry/current/.venv/bin/registry-mcp.sh --check
/path/to/deploy/registry/current/.venv/bin/registry-mcp-install-unit.sh \
  --port 8000 --registry /path/to/your/own/ports.json
```

`registry-mcp-install-unit.sh` renders the unit into *this release's own*
`share/registry-mcp/registry-mcp.service` (not `~/.config`) and registers it
with `systemctl --user link --force`, which creates a symlink in
`~/.config/systemd/user/` pointing back at that file -- so the real unit
content stays with the installed code, `~/.config` only ever holds a
pointer, and there's nothing to hand-edit or copy. It then runs
`daemon-reload` and `enable --now` (pass `--no-enable` to render + link
without starting anything). Safe to re-run any time (e.g. after changing
`--port`, or after a redeploy) -- `--force` makes the re-link a no-op
other than picking up the new `ExecStart`.

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
- Verified end to end, real (non-editable) `uv pip install` against the
  actual pushed `v0.1.0` tag on GitHub: package install, `share/` data-files
  landing correctly, `--check`, `registry-mcp-install-unit.sh` rendering +
  `systemctl --user link`-ing + enabling + starting a real unit, `curl
  /registry` on a custom port, `systemctl --user disable --now` cleanup --
  all in this dev sandbox (which also has a working `systemctl --user`).
  Not yet verified: the actual `mu2eai@mu2eaigpvm01` server account
  end-to-end, including whether its already-enabled linger carries through
  a `systemctl --user link`-registered unit the same way (no reason to
  expect otherwise, but not directly tested there).
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
