#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Unlike registry-mcp.sh/dqm-mcp.sh/ecl-mcp.sh, this one is NOT a no-op:
# runTool is a compiled Offline binary, only reachable after sourcing the
# Offline/mu2e environment. This runs ONCE, here, per server start -- NOT
# per tool call. The long-running MCP server process this script execs
# into (below) inherits the resulting PATH for its entire uptime, so every
# `subprocess.run(["runTool", ...])` call made from inside a tool handler,
# for the life of the process, finds the binary with no per-call setup
# cost. This is a deliberate design point, not an oversight -- see
# README.md.
#
# RUNS_MCP_MUSE_RELEASE picks which Offline release `muse setup` resolves.
# Confirmed "head" is a continuously-updated CI build published on cvmfs
# and does NOT depend on any local checkout's working directory (verified:
# resolves identically regardless of cwd). It's the only thing that exists
# right now for the runTool fixes/JSON-output work this server depends on.
# Once those changes are tagged and published as a real Offline release,
# point this at that tag instead -- see README.md's "Which muse release"
# section.
#
# `-u` is relaxed for this block only: museSetup.sh references at least one
# variable (MUSE_ERROR) that's fine under plain bash but fatal under
# `nounset` -- confirmed live ("unbound variable" abort with `-u` on).
# Restored immediately after, so the rest of this script keeps the
# protection `-u` gives against typos in our own code.
set +u
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup "${RUNS_MCP_MUSE_RELEASE:-head}"
set -u
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `runs-mcp` console script
# (same [tool.setuptools] script-files mechanism registry-mcp.sh uses), so
# this resolves regardless of which release's venv it's running from.
exec "$(dirname "$0")/runs-mcp" "$@"
