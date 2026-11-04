#!/usr/bin/env bash
# Claude Code MCP headersHelper: prints the HTTP headers for a token-gated
# Mu2e MCP server as a JSON object on stdout, e.g.
#   {"Authorization": "Bearer <MIKEY token>"}
#
# Claude Code runs this on every connect (sync_mcp_claude.py writes it as
# the server's "headersHelper"), so the token is never stored in
# ~/.claude.json and a rotated key is picked up without re-syncing.
#
# Runs inside the session, i.e. without Kerberos (the harness scrubs
# KRB5CCNAME), so it only works where getMu2eKey can read the key file
# directly (hosts mounting /exp) or a personal token file exists.
# sync-mcp.sh tests exactly that and falls back to a stored header if not.
#
# Exit non-zero (no output) if no token is available.

set -uo pipefail

# shellcheck source=mikey-token.sh
source "$(dirname "${BASH_SOURCE[0]}")/mikey-token.sh" 2>/dev/null

[[ -n "${MIKEY_TOKEN:-}" ]] || exit 1

python3 -c 'import json, os; print(json.dumps({"Authorization": "Bearer " + os.environ["MIKEY_TOKEN"]}))'
