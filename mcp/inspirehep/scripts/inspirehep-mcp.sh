#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything inspirehep-mcp needs loaded on the host before it
# can run, same slot registry-mcp.sh/dqm-mcp.sh/metacat-mcp.sh/arxiv-mcp.sh
# reserve. inspirehep-mcp itself has none: it's a plain HTTPS client
# (stdlib + requests) reaching INSPIRE-HEP's public REST API with no
# credentials, no mu2e/cvmfs offline-software environment. Example, if a
# future dependency ever needs it:
#
#   source /path/to/spack/share/spack/setup-env.sh
#   spack load some-module@version
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `inspirehep-mcp` console
# script (same [tool.setuptools] script-files mechanism the other four use),
# so this resolves regardless of which release's venv it's running from.
exec "$(dirname "$0")/inspirehep-mcp" "$@"
