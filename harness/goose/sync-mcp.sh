#!/usr/bin/env bash
# Checked before `set -euo pipefail` below -- see install.sh for why.
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
listed there into the LIVE, private goose config.yaml as a
streamable_http extension. Never touches config.yaml.example (the
tracked template) -- only the private file outside this repo.

Existing entries for the same server name are overwritten; anything else
already in config.yaml is left alone. Safe to re-run as the registry
changes.

For servers whose registry entry has "token": "yes", the Authorization
header is filled in from (in order):
  1. $MIKEY_TOKEN, if set
  2. /exp/mu2e/app/users/$USER/mikey_token (optional personal override)
  3. getMu2eKey -- the generic Mu2e key (CVMFS, or the vendored copy in
     ../shared/; needs a Kerberos ticket if the key isn't local)
See ../shared/mikey-token.sh.
If none is available, that server is still added, but disabled
(enabled: false) rather than skipped silently or left half-configured.
USAGE
  exit 2
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$script_dir/env.sh"

registry_url="${1:-http://mu2eaigpvm01.fnal.gov:8000/registry}"
config_file="$XDG_CONFIG_HOME/goose/config.yaml"

if [[ ! -f "$config_file" ]]; then
  echo "Error: no live config.yaml yet at $config_file -- run setup.sh first" >&2
  exit 1
fi

source "$script_dir/../shared/mikey-token.sh"

tmp_json="$(mktemp)"
trap 'rm -f "$tmp_json"' EXIT
echo "Fetching registry: $registry_url"
curl -fsSL "$registry_url" -o "$tmp_json"

python3 "$script_dir/sync_mcp.py" "$tmp_json" "$config_file"
