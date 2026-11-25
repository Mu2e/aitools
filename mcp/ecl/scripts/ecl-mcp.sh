#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything ecl-mcp needs loaded on the host before it can
# run. ecl-mcp itself has none: ecl-api's ECL client is a plain HTTP client
# (stdlib + requests) talking to the ECL's XML/REST endpoint over signed
# query-string requests, so it needs no mu2e/cvmfs offline-software
# environment. See dqm-mcp.sh's comment for why that matters (muse's
# exported PYTHONPATH shadowing pinned deps) -- same reasoning applies here.
#
# Credentials (ECL_URL, ECL_USER_NAME, ECL_PASSWORD) and MIKEY_KEYS_FILE
# come from the environment, not from flags here -- see
# ecl-mcp-install-unit.sh --env-file. Example of what a real setup hook
# would look like:
#
#   source /path/to/spack/share/spack/setup-env.sh
#   spack load some-module@version
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the venv's `python` (same
# [tool.setuptools] script-files mechanism registry-mcp.sh uses), so this
# resolves regardless of which release's venv it's running from.
#
# Deliberately `python -m ecl_mcp.server` rather than the `ecl-mcp` console
# script that the other servers' wrappers exec: ecl-api also declared an
# `ecl-mcp` entry point, and since both distributions install to the same
# bin/ecl-mcp, the dependency won and this wrapper silently ran ecl-api's
# pre-2.0 FastMCP server (ModuleNotFoundError: mcp.server.fastmcp under the
# mcp>=2 we pin). That alias is gone from ecl-api now, but the module path
# can't be hijacked by a dependency's entry point at all, so prefer it.
#
# PYTHONSAFEPATH keeps the cwd off sys.path, which `-m` would otherwise
# prepend and a console script never did -- the unit sets no
# WorkingDirectory=, so that cwd is $HOME, and a stray requests.py or mcp.py
# there would shadow a pinned dep (same hazard as dqm-mcp.sh's PYTHONPATH
# note). It's 3.11+; on 3.10 (our floor) it's ignored rather than an error,
# unlike the equivalent `-P` flag.
exec env PYTHONSAFEPATH=1 "$(dirname "$0")/python" -m ecl_mcp.server "$@"
