#!/usr/bin/env bash
set -euo pipefail

# --- Server-side environment setup -----------------------------------------
# Placeholder for anything agentlog-mcp needs loaded on the host before it can
# run. agentlog-mcp itself needs no mu2e/cvmfs offline-software environment: it
# is a plain Postgres client (psycopg) plus the MCP SDK.
#
# What it DOES need, and will refuse to start without:
#
#   * the database endpoint -- AGENTLOG_MCP_DB_HOST / _DB_PORT / _DB_NAME, or
#     the matching --db-* flags. No host, port or database name is baked into
#     this package, so one of the two has to supply them. Prefer the
#     --env-file passed to agentlog-mcp-install-unit.sh, which lands them in
#     the systemd unit rather than in this script.
#
#   * MIKEY_KEYS_FILE -- the key's name is recorded as every report's author,
#     which is the only accountability an append-only log has.
#
#   * a usable Kerberos credential for the database connection, which comes
#     from the ambient environment (a ticket cache or keytab-refreshed cache
#     belonging to the account this runs as). If that cache lives somewhere
#     non-default, set KRB5CCNAME here or -- better -- in the --env-file, e.g.
#
#       export KRB5CCNAME=FILE:/path/to/krb5cc_agentlog-mcp
# -----------------------------------------------------------------------------

# Installed by `uv pip install` as a sibling of the `agentlog-mcp` console
# script (the [tool.setuptools] script-files mechanism), so this resolves
# regardless of which release's venv it is running from.
exec "$(dirname "$0")/agentlog-mcp" "$@"
