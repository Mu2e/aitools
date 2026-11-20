#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  install.sh <deploy-root> <ref> [repo-url]

Installs agentlog-mcp into <deploy-root>/releases/<ref>/.venv using uv, pinned
to the given git ref (tag/branch/commit) of the aitools repo, via uv's git
subdirectory install syntax:

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --python <...>/.venv/bin/python \
    "agentlog-mcp @ git+<repo-url>@<ref>#subdirectory=mcp/agentlog"

This also pulls in mikey (same aitools repo, mcp/mikey) and psycopg.

That's the entire install -- no source tree is copied:

  <...>/.venv/bin/agentlog-mcp                      (python entry point)
  <...>/.venv/bin/agentlog-mcp.sh                   (bash wrapper -- environment
                                                     setup hook, execs agentlog-mcp)
  <...>/.venv/bin/agentlog-mcp-install-unit.sh      (renders + links the systemd
                                                     --user unit; see below)
  <...>/.venv/share/agentlog-mcp/create_tables.sql  (the schema DDL, for
                                                     reference -- it is NOT run
                                                     by this script; see below)
  <...>/.venv/share/agentlog-mcp/001-*.sql          (schema migrations, likewise)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd -- run the printed agentlog-mcp-install-unit.sh command when
ready.

Examples:
  ./scripts/install.sh /exp/mu2e/app/users/<account>/mcp/agentlog v0.8.0
  ./scripts/install.sh /exp/mu2e/app/users/<account>/mcp/agentlog main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service.
  - Requires `uv` on PATH.
  - FIRST INSTALL ONLY: the database schema must exist before the server will
    start. This script deliberately does not create it -- the MCP's own role
    cannot create tables, and schema changes should be a considered, manual
    act. Run it once, as a member of admin_role:
        psql "host=<db-host> port=<db-port> dbname=<db-name>" \
             -f <...>/share/agentlog-mcp/create_tables.sql
    Already have an agentlog schema from before 2026-11-19? Apply
    001-fix-range-projection-checks.sql as well.
  - NO ENDPOINT IS BUILT IN. agentlog-mcp ships no database host, port or name
    and no bind port, on purpose: those are deployment facts and this is a git
    repository. They are supplied to agentlog-mcp-install-unit.sh (which writes
    them into the systemd unit) or via its --env-file.
  - No credentials are needed in any config file: the database connection is
    Kerberos-authenticated from the ambient environment. MIKEY_KEYS_FILE is
    required (the key name is recorded as every report's author) and is passed
    to agentlog-mcp-install-unit.sh, not stored here.
USAGE
  exit 2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found on PATH" >&2
  exit 2
fi

deploy_root="$1"
ref="$2"
repo_url="${3:-https://github.com/Mu2e/aitools}"

release_dir="$deploy_root/releases/$ref"
current_link="$deploy_root/current"
venv_dir="$release_dir/.venv"

mkdir -p "$release_dir"

echo "[1/2] Creating venv: $venv_dir"
uv venv "$venv_dir"

echo "[2/2] Installing agentlog-mcp from ${repo_url}@${ref} (subdirectory: mcp/agentlog)"
uv pip install --python "$venv_dir/bin/python" \
  "agentlog-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/agentlog"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo
echo "Next steps:"
echo "  1. (first install only) create the schema, as a member of admin_role:"
echo "       psql \"host=<db-host> port=<db-port> dbname=<db-name>\" \\"
echo "            -f $venv_dir/share/agentlog-mcp/create_tables.sql"
echo "  2. check the configuration end to end (connects, files nothing):"
echo "       MIKEY_KEYS_FILE=/path/to/keys \\"
echo "       AGENTLOG_MCP_DB_HOST=<host> AGENTLOG_MCP_DB_PORT=<port> \\"
echo "       AGENTLOG_MCP_DB_NAME=<db> $venv_dir/bin/agentlog-mcp.sh --check"
echo "  3. $venv_dir/bin/agentlog-mcp-install-unit.sh --mikey-keys-file /path/to/keys \\"
echo "       --port <port> --db-host <host> --db-port <port> --db-name <db>"
echo "     (or --env-file <path> holding AGENTLOG_MCP_*; add --no-enable to skip"
echo "      enabling/starting the unit)"
