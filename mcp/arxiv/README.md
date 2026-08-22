# arxiv-mcp

Read-only streamable-HTTP MCP server for arXiv paper search, wrapping the
`arxiv` PyPI package (itself a thin client over arXiv's public Atom API).
Built from the start on the same uv-installed, systemd `--user`-run HTTP
pattern as `../registry`, `../dqm`, and `../metacat`: a `pyproject.toml`-based
package installed with plain `uv` primitives (`uv venv` + `uv pip install`,
via git subdirectory syntax) into a visible, versioned directory in a
service account, run persistently under `systemd --user`, and reachable
over HTTP.

## Scope

- Read-only operations only -- no write tools exposed
- No authentication -- arXiv's search API is fully open
- Streamable-HTTP transport
- No citation graph, author metrics, collaboration filter, or BibTeX --
  arXiv itself doesn't track those (see the planned `inspirehep` MCP for
  HEP-specific bibliographic workflows; arXiv and INSPIRE-HEP are
  complementary, not overlapping, data sources)

## Exposed MCP tools

- `search_papers(query, title, author, abstract, category, sort_by, sort_order, max_results)`
  - named filters (title/author/abstract/category) are AND-combined into
    arXiv's field-prefixed query syntax for you; `query` accepts that raw
    syntax directly (`ti:`/`au:`/`abs:`/`cat:`/`all:` with `AND`/`OR`/`ANDNOT`)
    for anything compound the named filters can't express -- both compose
    together if given at once
  - at least one of query/title/author/abstract/category is required
- `get_paper(arxiv_id)`
  - full metadata for one paper by arXiv id (e.g. `2301.12345`); read-only,
    returns `pdf_url` rather than downloading anything server-side
- `get_server_info()`
  - version, rate-limit configuration, capability notes

## How it's installed and started

A real (non-editable) `uv pip install` on this package produces, in the
target venv:

- `bin/arxiv-mcp` -- the Python entry point (`[project.scripts]`)
- `bin/arxiv-mcp.sh` -- a plain bash wrapper (`[tool.setuptools]
  script-files`), installed as a sibling of `arxiv-mcp` in the same
  directory. Server-side environment setup hook, currently a no-op
  passthrough -- `arxiv-mcp` itself needs no mu2e/cvmfs offline-software
  environment, just the `arxiv` package (a plain HTTPS client, no
  credentials), same as `registry-mcp.sh`/`dqm-mcp.sh`/`metacat-mcp.sh`.
- `bin/arxiv-mcp-install-unit.sh` -- renders and registers the systemd
  `--user` unit; see below.

No source tree gets copied, and nothing here needs a checkout to work
except `install.sh` itself (a convenience wrapper, not a requirement -- see
its own usage text for the fully manual 3-command alternative). All runtime
configuration (bind host/port, rate-limit knobs) is passed as CLI args at
start time, so the systemd unit's `ExecStart` is a single self-contained
line pointing at `arxiv-mcp.sh`, resolved relative to wherever that script
actually lives.

Versioned rollback works the same as the other three: each install lives
under `<deploy-root>/releases/<ref>/.venv`, and `<deploy-root>/current` is a
symlink to whichever release is active.

## Local dev run

On mu2e machines, get `uv` from the cluster's spack packages first:

```bash
mu2einit
slc uv
```

Then:

```bash
cd aitools/mcp/arxiv
uv venv
uv pip install -e .
.venv/bin/arxiv-mcp --host=127.0.0.1 --port=8003
```

(`.venv/bin/arxiv-mcp.sh` also works -- it's the same no-op-setup wrapper
the systemd unit calls.)

Startup compatibility check (imports/constructs only, no bind/listen):

```bash
.venv/bin/arxiv-mcp.sh --check
```

Smoke test against a running server (MCP handshake + tool calls, including
a live search against arXiv):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8003
```

## Config (CLI flags / env vars)

CLI flags are primary; the corresponding env var is used only as a default
when a flag isn't given:

| Flag | Env var | Default |
|---|---|---|
| `--host` | `ARXIV_MCP_HOST` | `0.0.0.0` |
| `--port` | `ARXIV_MCP_PORT` | `8003` |
| `--page-size` | `ARXIV_MCP_PAGE_SIZE` | `100` |
| `--delay-seconds` | `ARXIV_MCP_DELAY_SECONDS` | `3.0` |
| `--num-retries` | `ARXIV_MCP_NUM_RETRIES` | `3` |

`page_size`/`delay_seconds`/`num_retries` configure the underlying
`arxiv.Client` and follow arXiv's own requested rate-limit etiquette by
default -- not intended to be driven concurrently at high volume. Search
results are capped at 200 per call regardless of `max_results` requested.

**arXiv's rate limit is enforced per source IP, not per `Client` instance --
`delay_seconds`/`num_retries` only pace requests made *through this server*,
they cannot protect against other traffic sharing the same outbound IP**
(another process on the same box/account hitting arXiv directly, ad hoc
`curl`/debugging against `export.arxiv.org` outside the MCP, etc.). Exceeding
the real limit doesn't always come back as a clean fast error either --
confirmed while testing this server, a burst of unpaced requests to
`export.arxiv.org/api/query` got an explicit `429 Rate exceeded` on some
attempts, but on others the connection just hung (no response at all) for
well past a minute before the client's own retry/backoff gave up -- which
from the caller's side looks identical to the server being slow, not
rate-limited. If a search or lookup is taking unexpectedly long, suspect
this before assuming the server or arXiv itself is broken, and avoid
hammering `export.arxiv.org` with unpaced manual requests (`curl` etc.) from
the same host while this service is also running -- it draws from the same
budget.

`ARXIV_MCP_LOG_LEVEL` (default `INFO`) controls log verbosity.

## Server-account install

```bash
cd aitools/mcp/arxiv
./scripts/install.sh /path/to/deploy/arxiv v0.4.0
```

This creates `/path/to/deploy/arxiv/releases/v0.4.0/.venv` and symlinks
`/path/to/deploy/arxiv/current` to it. It does not touch systemd --
`install.sh` prints the next-step command for that:

```bash
/path/to/deploy/arxiv/current/.venv/bin/arxiv-mcp.sh --check
/path/to/deploy/arxiv/current/.venv/bin/arxiv-mcp-install-unit.sh --port 8003
```

`arxiv-mcp-install-unit.sh` renders the unit into *this release's own*
`share/arxiv-mcp/arxiv-mcp.service` (not `~/.config`) and registers it with
`systemctl --user link --force`, same pattern as the other three servers.
Requires linger enabled once per account so the service survives logout:

```bash
loginctl enable-linger        # or: sudo loginctl enable-linger <account>
```

## Client config

```json
{
  "mcpServers": {
    "arxiv": { "url": "http://<host>:8003/mcp" }
  }
}
```

Once deployed and reachable, register this URL/port in
`../registry/config/ports.json` so it shows up via `list_mcp_servers`/
`GET /registry`/`GET /list`.

## Notes

- Binds `0.0.0.0:8003` by default -- reachable across the org network;
  outside traffic is blocked by firewall. No auth is implemented (arXiv's
  own API needs none).
- Port convention for this workspace: `registry`=8000, `dqm`=8001,
  `metacat`=8002, `arxiv`=8003.
- Reaches `export.arxiv.org` over the public internet -- unlike `dqm`/
  `metacat` (both `fnal.gov` endpoints), confirm outbound network access
  from wherever this is deployed before relying on it.
- Version comes from the nearest git tag via `setuptools_scm`, same as the
  other three -- see `../registry/pyproject.toml`'s comment.
