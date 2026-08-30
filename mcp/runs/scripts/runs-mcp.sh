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
# `-e` and `-u` are both relaxed for this block only. Confirmed two
# distinct causes, live:
#   - museSetup.sh references at least one variable (MUSE_ERROR) that's
#     fine under plain bash but fatal under `nounset`.
#   - The legacy UPS-style /cvmfs/mu2e.opensciencegrid.org/artexternals/setup
#     this chain sources uses "return 1 from an internal helper" as a
#     routine, non-fatal "print a warning, keep going" idiom in several
#     places (e.g. the harmless "Please set shell or env. variable
#     prod_db" message) -- confirmed it's unset and warned-about even in
#     a normal interactive shell, and the script still exits 0 there.
#     Under `errexit` the first such internal nonzero return anywhere in
#     this whole legacy chain aborts the script immediately, which is
#     exactly what was crash-looping this service under systemd (it never
#     showed up interactively because an interactive shell doesn't run
#     with `-e`).
# Both restored immediately after, so the rest of this script keeps the
# protection `-eu` gives against mistakes in our own code.
set +eu
source /cvmfs/mu2e.opensciencegrid.org/setupmu2e-art.sh
muse setup "${RUNS_MCP_MUSE_RELEASE:-head}"
set -eu
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `runs-mcp` console script
# (same [tool.setuptools] script-files mechanism registry-mcp.sh uses), so
# this resolves regardless of which release's venv it's running from.
exec "$(dirname "$0")/runs-mcp" "$@"
