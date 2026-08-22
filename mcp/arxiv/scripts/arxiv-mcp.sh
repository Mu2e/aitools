#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything arxiv-mcp needs loaded on the host before it can
# run, same slot registry-mcp.sh/dqm-mcp.sh/metacat-mcp.sh reserve.
# arxiv-mcp itself has none: the 'arxiv' package (a pinned dependency of this
# venv) is a plain HTTPS client reaching arXiv's public Atom API with no
# credentials, no mu2e/cvmfs offline-software environment. Example, if a
# future dependency ever needs it:
#
#   source /path/to/spack/share/spack/setup-env.sh
#   spack load some-module@version
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `arxiv-mcp` console
# script (same [tool.setuptools] script-files mechanism the other three use),
# so this resolves regardless of which release's venv it's running from.
exec "$(dirname "$0")/arxiv-mcp" "$@"
