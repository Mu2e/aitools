#!/usr/bin/env bash
# Install the usage log service into <deploy-root>/releases/<ref>/.venv with
# uv, pinned to a git ref of aitools, and point <deploy-root>/current at it.
# The same layout as the MCP servers (mcp/memory/scripts/install.sh).
#
#   install.sh <deploy-root> <ref> [repo-url]
#   install.sh /exp/mu2e/app/users/mu2eai/usage-server main
#
# Run as the account that will run the service (mu2eai). Needs uv on PATH.
# Does not touch systemd: see the commands it prints.
set -euo pipefail

if [[ $# -lt 2 || $# -gt 3 ]]; then
  sed -n '2,/^set /s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" | sed '$d' >&2
  exit 2
fi
command -v uv >/dev/null 2>&1 || { echo "ERROR: uv not found on PATH" >&2; exit 2; }

deploy_root="$1"
ref="$2"
repo_url="${3:-https://github.com/Mu2e/aitools}"
release_dir="$deploy_root/releases/$ref"
venv_dir="$release_dir/.venv"

mkdir -p "$release_dir"
echo "[1/2] Creating venv: $venv_dir"
uv venv "$venv_dir"
echo "[2/2] Installing mu2e-usage-server from ${repo_url}@${ref} (subdirectory: harness/usage/server)"
uv pip install --python "$venv_dir/bin/python" \
  "mu2e-usage-server @ git+${repo_url}@${ref}#subdirectory=harness/usage/server"
ln -sfn "$release_dir" "$deploy_root/current"

echo "Done: $deploy_root/current -> $release_dir"
echo
echo "Next:"
echo "  1. the checks (key file, database):"
echo "       MIKEY_KEYS_FILE=/path/to/keys LOG_PG_DSN=postgresql://ifdb11:5477/mu2e_ai_prd \\"
echo "         KRB5CCNAME=FILE:/tmp/krb5cc_<uid>_auto $venv_dir/bin/usage-server --check"
echo "  2. the unit:"
echo "       $venv_dir/bin/usage-server-install-unit.sh --mikey-keys-file /path/to/keys \\"
echo "         --krb5-ccname FILE:/tmp/krb5cc_<uid>_auto"
