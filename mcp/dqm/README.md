# dqm-mcp (prototype)

Read-only streamable-HTTP MCP server for Mu2e DQM metrics using Query Engine
HTTP calls. Converted from the original stdio-transport server to the
uv-installed, systemd `--user`-run HTTP pattern proven out by `../registry`:
a `pyproject.toml`-based package, installed with plain `uv` primitives (`uv
venv` + `uv pip install`, via git subdirectory syntax) into a visible,
versioned directory in a service account, run persistently under `systemd
--user`, and reachable over HTTP instead of spawned per-connection over
stdio.

The query logic has since moved out of this package entirely, into the shared
`mu2e-dqm` distribution in the Mu2e/DQM repository — see the next section.

## Where the query logic lives

All database access is in **`dqm.reader`**, part of the **`mu2e-dqm`**
distribution in the [Mu2e/DQM](https://github.com/Mu2e/DQM) repository at
`DQM/python/dqm/`. This server holds no SQL, no CSV parsing, and no
schema knowledge beyond the tool descriptions — it is a thin, deliberately
limited window onto that library, shared with the DQM plotting scripts and
with bulk analysis work.

The split matters because the two callers want opposite things. `dqm.reader`
returns pandas DataFrames and is built to pull the whole table — `dqm.numbers`
holds ~475k rows today and is expected to reach millions, which as a DataFrame
is ~30 MB and a few seconds, and as per-row dicts would be near a gigabyte.
This server never wants more than a few hundred rows, because its output has to
fit in a model's context window. So it caps hard and converts to JSON at the
boundary; anything bigger is the library's job, called directly.

### How `mu2e-dqm` gets installed

It ships in a different repository, but it is a normal installable package, so
it comes in as a pinned git dependency — nothing extra to do:

```toml
dependencies = [
  "mcp>=2.0.0",
  "mu2e-dqm @ git+https://github.com/Mu2e/DQM@main#subdirectory=python"
]
```

The single `uv pip install "dqm-mcp @ git+..."` in `install.sh` pulls it in
along with its own `pandas`/`requests`. The `[web]` extra is deliberately not
requested — this server has no use for dash/plotly, and the single-distribution
layout means those modules ship as inert files it never imports. `--check`
prints the resolved `mu2e-dqm` version, and `get_server_info()` reports the
version and module path at runtime, so which reader a deployment is actually
running is never a guess.

> **Pin a tag.** The dependency currently tracks `main`, because `mu2e-dqm` is
> unreleased as of DQM `v1.4.0`. Once a DQM release contains it, change `@main`
> to that tag — tracking a branch means a rebuild can silently pick up reader
> changes never tested against this server.

To deploy against a different reader revision without editing the pin — trying
a reader branch before tagging it — `install.sh` takes `--dqm-ref`:

```bash
./scripts/install.sh /path/to/deploy/dqm v0.3.0 --dqm-ref my-branch
```

That installs the named revision *after* the main install, because pip resolves
a direct URL requirement to exactly that URL and would otherwise put the pinned
one back. Use it for testing; bump the pin in `pyproject.toml` for anything
permanent, so a release stays reproducible from its ref alone.

### Developing against a checkout

`DQM_READER_PATH` points at a DQM `python/` directory and takes precedence over
the installed copy, for working on the reader and this server together without
reinstalling between edits:

```bash
export DQM_READER_PATH=/path/to/DQM/python
```

It has to be an environment variable, not a CLI flag: the import happens at
module load, before argument parsing. In a systemd unit it would go in an
`Environment=` line — though for a deployment, pinning the dependency is the
better answer.

## Scope

- Read-only operations only
- Fixed dbname default: `mu2e_dqm_prd`
- Fixed endpoint default: QE nocache URL (`:9443`)
- Streamable-HTTP transport
- JSON table responses intended for client-side LLM interpretation
- Hard ceiling of 1000 rows per response, whatever `limit` is passed

## Exposed MCP tools

- `get_server_info()`
  - server defaults, backend `dqm.reader` version/path, QE endpoint/dbname, code enums
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
  - `summarize=true` — count/mean/min/max/median, time span, endpoints, and
    code tallies over *every* matching row, ignoring the display limit
  - `downsample_to=N` — a timeline reduced to about N time bins, each carrying
    the bin's mean, min and max so excursions survive the reduction

### Truncation and totals

Every response carries `counts.truncated`. When it is true,
`counts.total_rows` holds the *exact* number of matching rows, so a client can
report "showing 100 of 41,203" rather than leaving an ambiguous short result to
be misread as "there is no recent data here."

An empty result with `truncated=false` is authoritative. Filters are pushed
into the database rather than applied after a bounded scan, so an empty result
means no matching rows exist — it is not an artifact of a scan window. This
replaces the old `scan_limit`/`scan_complete` mechanism, which existed because
a filter that could not be pushed down fell back to scanning the most recent N
rows: a low-rate source sharing a table with high-rate ones could then have its
rows fall outside any scan window, no matter how large, because the required
window depended on everyone else's insertion rate rather than on the query's
own selectivity. `scan_limit` is gone; nothing replaces it, because nothing
needs to.

Note that `dqm.numbers` and `dqm.limits` are populated independently, with no
foreign key between them — an interval can legitimately carry limits and no
measurements. An empty `metric_table='numbers'` result does not imply the
interval is unused.

## How it's installed and started

A real (non-editable) `uv pip install` on this package produces, in the
target venv:

- `bin/dqm-mcp` -- the Python entry point (`[project.scripts]`)
- `bin/dqm-mcp.sh` -- a plain bash wrapper (`[tool.setuptools]
  script-files`), installed as a sibling of `dqm-mcp` in the same
  directory. It's the place for any server-side environment setup a real
  MCP might need (e.g. `spack load ...`) before it `exec`s its sibling
  `dqm-mcp`. It still needs no offline-software environment and the wrapper
  remains a no-op passthrough: through `dqm.reader` -- installed into this
  same venv as part of the pinned `mu2e-dqm` dependency -- it is a plain HTTP client (`requests` +
  `pandas`) reaching the Query Engine's public HTTPS endpoint with no
  credentials and no mu2e/cvmfs setup. (An earlier version of this wrapper sourced
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
| `--qe-timeout-seconds` | `DQM_QE_TIMEOUT_SECONDS` | `120` |
| *(none — see above)* | `DQM_READER_PATH` | unset (use the installed `mu2e-dqm`) |

`--qe-base-url` must point at the nocache endpoint (`:9443`); a `:8444/`
cache-endpoint URL is rejected at startup (by `dqm.reader` itself, so scripts
get the same protection).

The QE timeout default rose from 30s to 120s with the move to `dqm.reader`,
which is sized for bulk pulls — a full unfiltered table fetch takes a few
seconds today and will grow. Nothing this server issues comes close.

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
- All database reads go through `dqm.reader` over Query Engine HTTP.
- Default behavior is nocache endpoint use.
- Query defaults are intentionally conservative (`limit=100`, recent window
  10 days), and `limit` is clamped to 1000 no matter what is passed.
- For a result larger than the cap, use `summarize=true` or `downsample_to=N`
  rather than paging with `offset` — paging a 40,000-row timeline 100 at a
  time exhausts the context long before it finishes, and the summary answers
  "is this drifting?" just as well.
- For genuine bulk work (machine learning, retrospective analysis over the
  whole table), skip this server and import `dqm.reader` directly.
- Uses `mcp>=2.0.0` (`FastMCP` renamed to `MCPServer`, host/port moved from
  the constructor to `run()`) -- see `../registry/README.md`'s Notes section
  for the full API-change writeup, which applies identically here.
