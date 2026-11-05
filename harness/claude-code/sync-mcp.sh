#!/usr/bin/env bash
# Checked before `set -euo pipefail` below -- see goose/install.sh for why.
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  echo "Error: run this script, don't source it. Use: ./sync-mcp.sh" >&2
  return 1 2>/dev/null || exit 1
fi

set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  ./sync-mcp.sh [registry-url]

Fetches the MCP server registry (default:
http://mu2eaigpvm01.fnal.gov:8000/registry) and upserts every server
listed there into Claude Code's ~/.claude.json as user-scope HTTP MCP
servers. Existing entries are overwritten; anything else in ~/.claude.json
is left unchanged. Safe to re-run as the registry changes.

For servers whose registry entry has "token": "yes", the Authorization
header is filled in from (in order):
  1. $MIKEY_TOKEN, if set
  2. /exp/mu2e/app/users/$USER/mikey_token (optional personal override)
  3. getMu2eKey -- the generic Mu2e key (CVMFS, or the vendored copy in
     ../shared/; needs a Kerberos ticket if the key isn't local)
See ../shared/mikey-token.sh.
If none is available, that server is added with a disabled marker
rather than skipped or left half-configured.

Requires env.sh to have been sourced (for CLAUDE_HOME).
USAGE
  exit 2
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/env.sh"

registry_url="${1:-http://mu2eaigpvm01.fnal.gov:8000/registry}"
claude_json="$CLAUDE_HOME/.claude.json"

if [[ ! -f "$claude_json" ]]; then
  # Create a minimal ~/.claude.json so sync_mcp_claude.py can update it.
  # A real first-run creates this on first `claude` invocation, but sync
  # may run before that.
  ( umask 077 && printf '{}\n' > "$claude_json" )
  echo "Created $claude_json (minimal skeleton)"
fi

source "$script_dir/../shared/mikey-token.sh"

# Prefer a headersHelper (token fetched on each connect, never stored in
# ~/.claude.json) -- but only if it works the way Claude Code will run it:
# inside the session, without Kerberos. Otherwise store the header.
headers_helper="$(cd "$script_dir/../shared" && pwd)/mcp-headers.sh"
if [[ -n "$MIKEY_TOKEN" ]] && env -u MIKEY_TOKEN KRB5CCNAME=/nonexistent "$headers_helper" >/dev/null 2>&1; then
  export MCP_HEADERS_HELPER="$headers_helper"
else
  echo "Note: $headers_helper can't get the token without Kerberos on this host --" >&2
  echo "  storing the token in $claude_json instead (re-run sync-mcp.sh if it changes)." >&2
fi

tmp_json="$(mktemp)"
trap 'rm -f "$tmp_json"' EXIT

echo "Fetching registry: $registry_url"
curl -fsSL "$registry_url" -o "$tmp_json"

python3 "$script_dir/sync_mcp_claude.py" "$tmp_json" "$claude_json"
