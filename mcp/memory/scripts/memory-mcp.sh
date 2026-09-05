#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything memory-mcp needs loaded on the host before it can
# run. memory-mcp itself needs no mu2e/cvmfs offline-software environment: it
# is a plain Postgres client (psycopg) plus the MCP SDK.
#
# What it DOES need is a usable Kerberos credential for the database
# connection, which comes from the ambient environment (a ticket cache or
# keytab-refreshed cache belonging to the account this runs as). If that cache
# lives somewhere non-default, set KRB5CCNAME here or -- better -- in the
# --env-file passed to memory-mcp-install-unit.sh, so it lands in the systemd
# unit rather than being baked into this script. Example of a real setup hook:
#
#   export KRB5CCNAME=FILE:/path/to/krb5cc_memory-mcp
#
# MIKEY_KEYS_FILE is required and also comes from the environment; the server
# refuses to start without it (see server.py's _preflight).
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `memory-mcp` console script
# (same [tool.setuptools] script-files mechanism registry-mcp.sh uses), so this
# resolves regardless of which release's venv it's running from.
exec "$(dirname "$0")/memory-mcp" "$@"
