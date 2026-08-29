"""runs-mcp: streamable-HTTP MCP server for the Mu2e run database, via the
`runTool` CLI (a subprocess wrapper, not a SWIG/Python binding -- see
"Why subprocess, not SWIG" in README.md).

runTool is a compiled Offline binary, only reachable after sourcing the
Offline/mu2e environment (`muse setup`). That setup runs ONCE, at server
startup, in this package's `runs-mcp.sh` wrapper script -- NOT per tool
call. The long-running MCP server process this script execs into inherits
that PATH for its entire uptime, so every `subprocess.run(["runTool", ...])`
call made from a tool handler, for the life of the process, reaches the
binary with no per-call setup cost. This module assumes `runTool` is
already on PATH by the time it runs; it does not attempt to source
anything itself.

There is no read/write distinction here (unlike ecl-mcp/dqm-mcp) -- runTool
itself exposes no write operations, so this server is structurally
read-only.
"""

from __future__ import annotations

import argparse
import functools
import importlib.metadata
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

from mcp.server.mcpserver import MCPServer
from mikey import build_auth_kwargs

LOGGER = logging.getLogger("runs_mcp")

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8006
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_BLOB_TIMEOUT_SECONDS = 60

# Hard ceiling on list_runs's result size, regardless of what the caller
# asks for -- an unfiltered `runTool -j` returns every run in the database
# (796 and growing at the time this was written; nothing bounds that
# growth), which would be many thousands of lines of JSON dumped straight
# into an agent's context. list_runs always passes -n under the hood
# (confirmed it composes safely and cheaply with every other filter,
# including a full-history -r range -- see README.md), clamped to this
# ceiling no matter what `last` value is requested.
MAX_LAST_RUNS = 200
DEFAULT_LAST_RUNS = 50

# Hard ceiling on get_config_blob's search match count.
MAX_BLOB_MATCHES = 100
DEFAULT_BLOB_MATCHES = 20


def _server_version() -> str:
    try:
        return importlib.metadata.version("runs-mcp")
    except importlib.metadata.PackageNotFoundError:
        return "unknown (not installed as a package)"


@dataclass
class _Config:
    runtool_path: str = os.environ.get("RUNS_MCP_RUNTOOL_PATH", "runTool")
    timeout_seconds: int = int(os.environ.get("RUNS_MCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)))
    blob_timeout_seconds: int = int(
        os.environ.get("RUNS_MCP_BLOB_TIMEOUT_SECONDS", str(DEFAULT_BLOB_TIMEOUT_SECONDS))
    )


# Populated with env-var-backed defaults at import time (so importing this
# module without calling main() -- e.g. tests -- still works), overridden
# by main() from parsed CLI args.
_config = _Config()

INSTRUCTIONS = (
    "Read-only MCP server for the Mu2e run database, via the `runTool` CLI. "
    "There is no write capability -- runTool itself exposes none.\n\n"
    "DATA MODEL: a run has a type (see get_flags for the id->name mapping), "
    "zero or more subsystem configs (one version per subsystem active for "
    "that run), zero or more transitions (state changes -- start/stop/"
    "stop_complete/error/pause/resume/halt/halt_complete, also in "
    "get_flags), and zero or more subruns (event-count/time-window "
    "segments). Config/transition/subrun detail is opt-in per call "
    "(configs=/transitions=/subruns=) since most callers just want a run's "
    "own top-level fields.\n\n"
    "Typical flow: list_runs to find run(s) of interest (always capped "
    "server-side -- see its own docstring), get_run for one run's full "
    "detail, get_flags for the type-id mappings. get_dbtables/"
    "get_cidtables cover the underlying cat-3/cat-2 DbService tables for "
    "exactly one run -- rarely needed, mostly for debugging specific "
    "database content. get_config_blob is the one to use carefully: "
    "config blobs are several MB and take ~1-2s to fetch even just for a "
    "summary, and this tool deliberately never returns full blob content "
    "-- see its own docstring for the summary/search-only design."
)

mcp = MCPServer("runs", instructions=INSTRUCTIONS, **build_auth_kwargs())


def _run_runtool(args: list[str], timeout: float) -> Any:
    """Run `runTool <args> -j`, parse stdout as JSON.

    Raises ValueError (-> {"error": "bad_request", ...} via _wrap) if
    runTool ran but rejected the input (bad selector, multi-run on a
    single-run-only accessor, etc.) -- the caller can fix this by changing
    arguments. Raises RuntimeError (-> {"error": "tool_failed", ...}) if
    runTool couldn't run at all or timed out -- an infrastructure problem,
    not something a different argument value fixes.
    """
    argv = [_config.runtool_path, *args, "-j"]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError(
            f"'{_config.runtool_path}' not found on PATH -- this server's wrapper "
            "script must source the Offline environment (muse setup) before "
            "starting. See runs-mcp.sh."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"runTool timed out after {timeout}s: {' '.join(argv)}")

    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or f"runTool exited {result.returncode} with no stderr output")
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)


def _wrap(fn):
    """Convert exceptions raised inside a tool into structured error dicts.

    Keeps the agent-facing contract simple: every tool either returns its
    normal payload or {"error": "<code>", "message": "<detail>"}.
    """

    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ValueError as e:
            return {"error": "bad_request", "message": str(e)}
        except Exception as e:  # noqa: BLE001 -- deliberately broad, see docstring
            LOGGER.exception("runs-mcp tool %s failed", fn.__name__)
            return {"error": "tool_failed", "message": f"{type(e).__name__}: {e}"}

    return inner


@mcp.tool(description="Get runs-mcp configuration and version info.")
def get_server_info() -> dict[str, Any]:
    return {
        "name": "runs",
        "version": _server_version(),
        "transport": "streamable-http",
        "auth": "mikey" if mcp.settings.auth else "disabled",
        "runtool_path": _config.runtool_path,
    }


@mcp.tool(name="get_run")
@_wrap
def get_run(
    run: int,
    configs: bool = False,
    transitions: bool = False,
    subruns: bool = False,
) -> dict[str, Any]:
    """Fetch one run's detail by run number.

    Args:
        run: the run number
        configs: also include the subsystem config versions active for this run
        transitions: also include this run's state transitions (start/stop/...)
        subruns: also include this run's subrun event-count/time segments

    Returns the run's fields (run, type_id, type_name, create_time, plus
    configs/transitions/subruns arrays -- empty unless requested above), or
    {"error": "not_found", ...} if no run with that number exists.
    """
    args = ["-r", str(run)]
    if configs:
        args.append("-c")
    if transitions:
        args.append("-a")
    if subruns:
        args.append("-s")
    result = _run_runtool(args, _config.timeout_seconds)
    if not result:
        return {"error": "not_found", "message": f"no run {run} found"}
    return result[0]


@mcp.tool(name="list_runs")
@_wrap
def list_runs(
    run_range: str = "",
    last: int = DEFAULT_LAST_RUNS,
    type_ids: str = "",
    time_range: str = "",
    days: int | None = None,
    configs: bool = False,
    transitions: bool = False,
    subruns: bool = False,
) -> list[dict[str, Any]]:
    """List runs matching optional filters, most recent first.

    IMPORTANT: `last` always caps the result server-side -- there is no way
    to get an unbounded listing through this tool, even by combining
    filters that would otherwise match everything. This is deliberate: the
    run database currently has ~800 runs and grows without bound, and an
    unfiltered listing would be many thousands of lines of JSON.

    Args:
        run_range: runTool's own selector syntax, e.g. "124153" or
                   "124000-124265". Composes with last (e.g. a huge range
                   plus last=5 still only returns the most recent 5 in
                   that range, cheaply -- confirmed, does not scan the
                   whole range first).
        last: max runs to return, most recent first. Default 50, hard
              ceiling 200 regardless of what's requested.
        type_ids: comma-separated run-type ids to restrict to, e.g. "1,3"
                  -- see get_flags for what each id means.
        time_range: ISO8601 "since" (e.g. "2026-06-05T18:38:20-05:00") or
                    "since/until" range (e.g. "2026-06-01/2026-06-05").
        days: restrict to runs created in the last N days.
        configs/transitions/subruns: same as get_run -- opt-in detail,
                                      applied to every returned run.
    """
    last = max(1, min(last, MAX_LAST_RUNS))
    args = ["-n", str(last)]
    if run_range:
        args += ["-r", run_range]
    if type_ids:
        args += ["-y", type_ids]
    if time_range:
        args += ["-t", time_range]
    if days is not None:
        args += ["-d", str(days)]
    if configs:
        args.append("-c")
    if transitions:
        args.append("-a")
    if subruns:
        args.append("-s")
    return _run_runtool(args, _config.timeout_seconds) or []


@mcp.tool(name="get_flags")
@_wrap
def get_flags() -> dict[str, Any]:
    """Return the run-type and transition-type enum mappings (id -> name).

    Static reference data (run_flags, transition_flags) -- cheap, no
    per-run cost.
    """
    return _run_runtool(["-f"], _config.timeout_seconds)


@mcp.tool(name="get_dbtables")
@_wrap
def get_dbtables(run: int) -> dict[str, Any]:
    """Cat-3 DbService tables for exactly one run.

    Mostly for debugging specific database content -- rarely needed for
    ordinary run-status questions. Rejects (as a bad_request error) if
    run doesn't resolve to exactly one run.
    """
    return _run_runtool(["-r", str(run), "-q"], _config.timeout_seconds) or {}


@mcp.tool(name="get_cidtables")
@_wrap
def get_cidtables(run: int) -> list[dict[str, Any]]:
    """Cat-2 DbService CID tables (cid -> name) for exactly one run.

    Mostly for debugging specific database content -- rarely needed for
    ordinary run-status questions.
    """
    return _run_runtool(["-r", str(run), "-e"], _config.timeout_seconds) or []


def _flatten(obj: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Flatten a nested dict/list into (dotted-path, leaf-value) pairs."""
    out: list[tuple[str, Any]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.extend(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out.extend(_flatten(v, f"{prefix}[{i}]"))
    else:
        out.append((prefix, obj))
    return out


@mcp.tool(name="get_config_blob")
@_wrap
def get_config_blob(
    run: int,
    subsystem: str,
    query: str = "",
    max_matches: int = DEFAULT_BLOB_MATCHES,
) -> dict[str, Any]:
    """Fetch one subsystem's configuration blob for one run -- SUMMARY BY DEFAULT.

    Config blobs are large (can be multiple MB of text) and slow to fetch
    (~1-2s server-side, a real database call every time -- there is no
    cache). To keep both the fetch cost and the response size sane:

    - query="" (the default): returns only {found, size_bytes,
      top_level_keys} -- never the actual settings content. Use this to
      check whether a subsystem's config exists and see its shape before
      deciding whether you need any of it.
    - query set: the blob is fetched (same ~1-2s cost -- that part can't
      be avoided), flattened into dotted key paths, and searched
      (case-insensitive substring match against the path or the value).
      Only matching {path, value} pairs are returned, capped at
      max_matches (default 20, hard ceiling 100), each value truncated to
      300 characters.

    There is currently no tool that returns a full blob's content --
    config blobs are not meant to be read end-to-end by an agent; do
    targeted lookups via query instead.

    Args:
        run: the run number
        subsystem: e.g. "TRG", "CRV", "CFO", "DQM", "Gateway"
        query: substring to search for (case-insensitive) in flattened
               key paths and values; empty means summary-only
        max_matches: cap on returned matches when query is set
    """
    result = _run_runtool(["-r", str(run), "-b", subsystem], _config.blob_timeout_seconds)
    entry = (result or [{}])[0]
    if not entry.get("found"):
        return {"run": run, "subsystem": subsystem, "found": False}

    settings = entry.get("settings")
    size_bytes = len(json.dumps(settings))

    if not query:
        top_level_keys = sorted(settings.keys()) if isinstance(settings, dict) else None
        return {
            "run": run,
            "subsystem": subsystem,
            "found": True,
            "size_bytes": size_bytes,
            "top_level_keys": top_level_keys,
        }

    capped = max(1, min(max_matches, MAX_BLOB_MATCHES))
    needle = query.lower()
    matches = []
    for path, value in _flatten(settings):
        value_str = str(value)
        if needle in path.lower() or needle in value_str.lower():
            if len(value_str) > 300:
                value_str = value_str[:300] + "...(truncated)"
            matches.append({"path": path, "value": value_str})
            if len(matches) >= capped:
                break

    return {
        "run": run,
        "subsystem": subsystem,
        "found": True,
        "size_bytes": size_bytes,
        "query": query,
        "match_count": len(matches),
        "matches": matches,
        "capped": len(matches) >= capped,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="runs-mcp", description=__doc__)
    parser.add_argument(
        "--host",
        default=os.environ.get("RUNS_MCP_HOST", DEFAULT_HOST),
        help="bind address (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("RUNS_MCP_PORT", str(DEFAULT_PORT))),
        help="bind port (default: %(default)s)",
    )
    parser.add_argument(
        "--runtool-path",
        default=os.environ.get("RUNS_MCP_RUNTOOL_PATH", "runTool"),
        help="path to the runTool binary (default: %(default)s, i.e. resolved via PATH)",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=int(os.environ.get("RUNS_MCP_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))),
        help="timeout for ordinary runTool calls (default: %(default)s)",
    )
    parser.add_argument(
        "--blob-timeout-seconds",
        type=int,
        default=int(os.environ.get("RUNS_MCP_BLOB_TIMEOUT_SECONDS", str(DEFAULT_BLOB_TIMEOUT_SECONDS))),
        help="timeout for get_config_blob's runTool calls, which are slower (default: %(default)s)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "import/construct only, then exit -- does not bind a socket. "
            "Runs `runTool -f` as a real check that runTool is on PATH and "
            "the database is reachable."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(
        level=os.environ.get("RUNS_MCP_LOG_LEVEL", "INFO"),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args(argv)

    _config.runtool_path = args.runtool_path
    _config.timeout_seconds = args.timeout_seconds
    _config.blob_timeout_seconds = args.blob_timeout_seconds

    if args.check:
        try:
            _run_runtool(["-f"], _config.timeout_seconds)
        except Exception as e:
            print(f"FAILED: {e}", file=sys.stderr)
            sys.exit(1)
        print(
            f"OK: runs_mcp constructed "
            f"(host={args.host} port={args.port} runtool={_config.runtool_path} "
            f"auth={'mikey' if mcp.settings.auth else 'disabled'})"
        )
        return

    LOGGER.info(
        "Starting runs MCP server over streamable-http on %s:%s (runtool=%s, auth=%s)",
        args.host,
        args.port,
        _config.runtool_path,
        "mikey" if mcp.settings.auth else "disabled",
    )
    mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
