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

# Installed by `uv pip install` as a sibling of the `ecl-mcp` console script
# (same [tool.setuptools] script-files mechanism registry-mcp.sh uses), so
# this resolves regardless of which release's venv it's running from.
exec "$(dirname "$0")/ecl-mcp" "$@"
