#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything metacat-mcp needs loaded on the host before it can
# run, same slot registry-mcp.sh and dqm-mcp.sh reserve. metacat-mcp itself
# has none: metacat.webapi.MetaCatClient comes from the pip-installable
# `metacat-client` package (a pinned dependency of this venv) and talks to
# the MetaCat server over plain HTTPS, configured entirely via the
# METACAT_SERVER_URL / METACAT_AUTH_SERVER_URL environment variables -- no
# mu2e/cvmfs offline-software environment and no `spack load` needed. An
# earlier version of this server sourced `setupmu2e-art.sh` + `muse setup
# ops` to reach a spack-built `metacat` client; that dependency is gone now
# that `metacat-client` ships on PyPI. Example, if a future dependency ever
# needs it:
#
#   source /path/to/spack/share/spack/setup-env.sh
#   spack load some-module@version
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `metacat-mcp` console
# script (same [tool.setuptools] script-files mechanism registry-mcp.sh and
# dqm-mcp.sh use), so this resolves regardless of which release's venv it's
# running from.
exec "$(dirname "$0")/metacat-mcp" "$@"
