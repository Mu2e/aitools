#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  install.sh <deploy-root> <ref> [repo-url]

Installs memory-mcp into <deploy-root>/releases/<ref>/.venv using uv, pinned to
the given git ref (tag/branch/commit) of the aitools repo, via uv's git
subdirectory install syntax:

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --python <...>/.venv/bin/python \
    "memory-mcp @ git+<repo-url>@<ref>#subdirectory=mcp/memory"

This also pulls in mikey (same aitools repo, mcp/mikey) and psycopg.

That's the entire install -- no source tree is copied:

  <...>/.venv/bin/memory-mcp                     (python entry point)
  <...>/.venv/bin/memory-mcp.sh                   (bash wrapper -- environment
                                                    setup hook, execs memory-mcp)
  <...>/.venv/bin/memory-mcp-install-unit.sh      (renders + links the systemd
                                                    --user unit; see below)
  <...>/.venv/share/memory-mcp/create_tables.sql  (the schema DDL, for
                                                    reference -- it is NOT run
                                                    by this script; see below)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd -- run the printed memory-mcp-install-unit.sh command when ready.

Examples:
  ./scripts/install.sh /exp/mu2e/app/users/mu2eai/mcp/memory v0.7.0
  ./scripts/install.sh /exp/mu2e/app/users/mu2eai/mcp/memory main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service.
  - Requires `uv` on PATH.
  - FIRST INSTALL ONLY: the database schema must exist before the server will
    start. This script deliberately does not create it -- the MCP's own role
    cannot create tables, and schema changes should be a considered, manual
    act. Run it once, as a user with DDL rights:
        psql -h ifdb11 -p 5477 mu2e_ai_prd -f sql/create_tables.sql
  - No credentials are needed in any config file: the database connection is
    Kerberos-authenticated from the ambient environment. MIKEY_KEYS_FILE is
    required (the key name is the document owner) and is passed to
    memory-mcp-install-unit.sh, not stored here.
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

echo "[2/2] Installing memory-mcp from ${repo_url}@${ref} (subdirectory: mcp/memory)"
uv pip install --python "$venv_dir/bin/python" \
  "memory-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/memory"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo
echo "Next steps:"
echo "  1. (first install only) create the schema, as a user with DDL rights:"
echo "       psql -h ifdb11 -p 5477 mu2e_ai_prd -f $venv_dir/share/memory-mcp/create_tables.sql"
echo "  2. MIKEY_KEYS_FILE=/path/to/keys $venv_dir/bin/memory-mcp.sh --check"
echo "  3. $venv_dir/bin/memory-mcp-install-unit.sh --port 8007 --mikey-keys-file /path/to/keys"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
