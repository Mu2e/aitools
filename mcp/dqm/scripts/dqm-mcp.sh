#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything dqm-mcp needs loaded on the host before it can
# run. dqm-mcp itself has none: it's a plain HTTP client (stdlib + requests)
# talking to the Query Engine's public HTTPS endpoint over anonymous
# query-string GETs, so it needs no mu2e/cvmfs offline-software environment
# and no credentials. An earlier version of this wrapper sourced
# `setupmu2e-art.sh` + `muse setup ops` here (carried over from the old
# stdio launcher); that was verified unnecessary -- confirmed reachable in a
# fully stripped environment (`env -i`, no Kerberos ticket) -- and actively
# harmful, since muse's exported PYTHONPATH shadowed this venv's pinned
# mcp>=2.0.0 deps with older cvmfs/spack copies. This file exists to
# establish the pattern for any future setup a real need would require, same
# slot registry-mcp.sh reserves. Example:
#
#   source /path/to/spack/share/spack/setup-env.sh
#   spack load some-module@version
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `dqm-mcp` console script
# (same [tool.setuptools] script-files mechanism registry-mcp.sh uses), so
# this resolves regardless of which release's venv it's running from.
exec "$(dirname "$0")/dqm-mcp" "$@"
