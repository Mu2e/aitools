# runs-mcp

Read-only streamable-HTTP MCP server for the Mu2e run database, via the
`runTool` CLI. mikey-authenticated (auth stays off unless `MIKEY_KEYS_FILE`
is set, same as `ecl-mcp`/`mikey`'s general pattern).

## Why subprocess, not SWIG

`Offline/DbService` ships both a CLI (`runTool`) and, via SWIG, a Python
binding of the same C++ library (`import DbService`). The Python binding
was considered and rejected for this server: it's a compiled extension
built against Offline's Python (3.10), and this server runs under `uv`'s
Python (3.13) -- that's a CPython ABI mismatch, not a `PYTHONPATH` problem,
and there is no fix for it short of not crossing that boundary at all.
Shelling out to the compiled `runTool` binary instead sidesteps the
mismatch entirely, at the cost of parsing CLI output instead of calling
methods directly -- `runTool` already has a `-j`/JSON mode for every
output shape this server uses, so that cost is small.

## `runTool` needs the Offline environment -- once, at startup, not per call

`runTool` is only reachable after sourcing Offline's environment (`muse
setup`). `runs-mcp.sh` (not a no-op, unlike the other servers' wrapper
scripts) does this once, before `exec`-ing into the actual long-running
MCP server process:

```bash
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup "${RUNS_MCP_MUSE_RELEASE:-head}"
exec runs-mcp "$@"
```

Because `exec` preserves the environment and the server process then runs
for its entire uptime, every subsequent `subprocess.run(["runTool", ...])`
call made from inside a tool handler inherits that `PATH` for free --
`muse setup` never runs again until the process restarts.

### Which muse release

`RUNS_MCP_MUSE_RELEASE` defaults to `head` -- confirmed to resolve to a
continuously-updated CI build published on cvmfs
(`/cvmfs/mu2e-development.opensciencegrid.org/museCIBuild/main/...`),
independent of any local checkout's working directory. That's what exists
today for the `runTool` fixes and JSON-output work this server depends on
(crash fixes, `-j` support across all output modes, multi-run rejection
on single-run-only accessors -- see git history in
`Offline/DbService/src`). Once that work is tagged and published as a real
Offline release, point `RUNS_MCP_MUSE_RELEASE` at that tag instead of
`head` -- `head` is a moving target, fine for getting this running, not
what you want for a long-lived production deployment.

## Exposed tools

- `get_server_info()` -- version/config.
- `get_run(run, configs=, transitions=, subruns=)` -- one run's detail by
  number. `{"error": "not_found", ...}` if it doesn't exist.
- `list_runs(run_range=, last=, type_ids=, time_range=, days=, configs=,
  transitions=, subruns=)` -- filtered listing, most recent first.
  **Always capped server-side at 200 rows regardless of `last`** (default
  50) -- the run database currently has ~800 runs with no bound on growth,
  and an unfiltered `runTool -j` returns every single one (confirmed: 796
  runs, 7166 lines of JSON, at the time this was written). There is no way
  to get an unbounded listing through this tool.
- `get_flags()` -- the run-type/transition-type id-to-name mappings. Cheap,
  static.
- `get_dbtables(run)` / `get_cidtables(run)` -- the underlying cat-3/cat-2
  DbService tables for exactly one run. Mostly for debugging specific
  database content, not ordinary run-status questions.
- `get_config_blob(run, subsystem, query=, max_matches=)` -- see below.

No write tools exist -- `runTool` itself has no write mode, so this server
is structurally read-only (unlike `ecl-mcp`, there's no `READ_ONLY` toggle
to reason about).

## `get_config_blob`: summary-only by default, search instead of dump

Config blobs are large (a single subsystem's blob measured at 664KB during
testing) and slow (~1-2s per fetch, a real, uncached database call every
time -- confirmed live, not estimated). Dumping one into an agent's
context by default would be exactly the wrong tradeoff for how rarely the
full content is actually needed, so this tool never returns full content:

- `query=""` (default): fetches the blob (still ~1-2s -- that part can't
  be avoided) but returns only `{found, size_bytes, top_level_keys}`.
  Confirmed in testing: a 664,535-byte blob collapses to a 151-character
  response this way.
- `query` set: the blob is flattened into dotted key paths (e.g.
  `config.tables.DesktopIconTable-v134.DATA_SET[0].IMAGE_URL`) and
  case-insensitive substring-matched against both path and value. Only
  matches are returned, capped at `max_matches` (default 20, hard ceiling
  100), each value truncated to 300 characters.

There is deliberately no tool that returns a blob's full content end to
end. If a real need for that shows up later, it should probably go through
a different mechanism (e.g. a file written somewhere both the server and
a human/script can reach) rather than a giant MCP tool-call response --
not implemented here since, per the design discussion that led to this
server, that pattern is expected to be rare.

## Environment variables

| Variable                     | Default   | Purpose                                          |
|-------------------------------|-----------|---------------------------------------------------|
| `RUNS_MCP_MUSE_RELEASE`      | `head`    | `muse setup` argument, read by `runs-mcp.sh` (not the Python server itself) |
| `RUNS_MCP_HOST`              | `0.0.0.0` | HTTP bind host                                    |
| `RUNS_MCP_PORT`              | `8006`    | HTTP bind port                                    |
| `RUNS_MCP_RUNTOOL_PATH`      | `runTool` | override if not resolving `runTool` via `PATH`    |
| `RUNS_MCP_TIMEOUT_SECONDS`   | `30`      | timeout for ordinary `runTool` calls              |
| `RUNS_MCP_BLOB_TIMEOUT_SECONDS` | `60`   | timeout for `get_config_blob`'s calls (slower)    |
| `RUNS_MCP_LOG_LEVEL`         | `INFO`    | logger level                                      |
| `MIKEY_KEYS_FILE`            | _(unset)_ | path to mikey's shared keys file -- unset means auth stays off |
| `MIKEY_SERVER_URL`           | _(unset)_ | see mikey's README -- placeholder OAuth field, rarely needs setting |

None of these are credentials -- `runTool` needs no secrets (same ambient
Query-Engine access pattern as `dqm-mcp`), so unlike `ecl-mcp` there's no
`--env-file`/`EnvironmentFile=` machinery here.

## `--check`

Runs `runTool -f` for real (cheap, fast) as a genuine check that the
binary is on `PATH` and the database is reachable -- fails clearly (exit
1, one-line message) if not, same pattern as `ecl-mcp`'s `--check`.

## Local dev

```bash
mu2einit  # or: source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup head
slc uv
cd aitools/mcp/runs
uv venv
uv pip install -e .
.venv/bin/runs-mcp --check
.venv/bin/runs-mcp --port=8006
```

Note: running `.venv/bin/runs-mcp` directly (not `.venv/bin/runs-mcp.sh`)
skips the `muse setup` step -- fine for local dev if you've already sourced
the Offline environment yourself in that shell (as above), but the real
deployed entry point is always `runs-mcp.sh`.

Smoke test against a running server (pass a mikey token as the second
argument if auth is enabled; omit it only if `MIKEY_KEYS_FILE` is unset):

```bash
.venv/bin/python scripts/smoke_test_http.py http://127.0.0.1:8006 mikey_<token>
```

## Server-account install

```bash
cd aitools/mcp/runs
./scripts/install.sh /path/to/deploy/runs v0.1.0
/path/to/deploy/runs/current/.venv/bin/runs-mcp.sh --check
/path/to/deploy/runs/current/.venv/bin/runs-mcp-install-unit.sh \
  --port 8006 --mikey-keys-file /path/to/shared/keys.json
```

See `scripts/runs-mcp-install-unit.sh --help` for all options
(`--muse-release`, `--timeout-seconds`, etc.).

## Client config

```bash
claude mcp add --transport http --scope user runs http://<host>:8006/mcp \
  --header "Authorization: Bearer <mikey-token>"
```

`--scope user` or `--scope local` -- never `--scope project` (see mikey's
README for why).
