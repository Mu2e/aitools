#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything registry-mcp needs loaded on the host before it
# can run (e.g. `module load` / `spack load` for a real MCP's dependencies).
# registry-mcp itself has none; this file exists to establish the pattern
# for future HTTP MCPs that do need it. Example:
#
#   source /path/to/spack/share/spack/setup-env.sh
#   spack load some-module@version
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `registry-mcp` console
# script (same [tool.setuptools] script-files mechanism), so this resolves
# regardless of which release's venv it's running from.
exec "$(dirname "$0")/registry-mcp" "$@"
