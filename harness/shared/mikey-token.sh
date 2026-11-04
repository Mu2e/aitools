# Sourced helper (not a standalone script): resolves the MIKEY token used
# as the Bearer token for token-gated MCP servers, and exports it as
# MIKEY_TOKEN (empty if nothing could be found -- callers then add those
# servers disabled rather than failing).
#
# Precedence:
#   1. $MIKEY_TOKEN, if already set
#   2. /exp/mu2e/app/users/$USER/mikey_token -- optional personal override
#      (private, 0600)
#   3. getMu2eKey -- the generic Mu2e key. The CVMFS original is preferred;
#      the vendored copy next to this file is the fallback for hosts
#      without CVMFS. getMu2eKey reads the key locally if visible, else
#      fetches it over ssh using your Kerberos ticket.
#
# Run from the harness setup/sync scripts in YOUR shell, never from inside
# an agent session: the harness wrappers scrub KRB5CCNAME and the
# guardrails block ssh there, so step 3 can't work in that context anyway.

_mikey_personal_file="/exp/mu2e/app/users/$USER/mikey_token"
_mikey_cvmfs_getkey="/cvmfs/mu2e.opensciencegrid.org/bin/getMu2eKey"
_mikey_local_getkey="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/getMu2eKey"

if [[ -z "${MIKEY_TOKEN:-}" && -s "$_mikey_personal_file" ]]; then
  MIKEY_TOKEN="$(cat "$_mikey_personal_file")"
  echo "MIKEY token: using personal override $_mikey_personal_file" >&2
fi

if [[ -z "${MIKEY_TOKEN:-}" ]]; then
  if [[ -x "$_mikey_cvmfs_getkey" ]]; then
    _mikey_getkey="$_mikey_cvmfs_getkey"
  else
    _mikey_getkey="$_mikey_local_getkey"
  fi
  if MIKEY_TOKEN="$("$_mikey_getkey")" && [[ -n "$MIKEY_TOKEN" ]]; then
    echo "MIKEY token: obtained via $_mikey_getkey" >&2
  else
    MIKEY_TOKEN=""
    echo "Warning: no MIKEY token ($_mikey_getkey failed) -- token-gated" >&2
    echo "  MCP servers will be added disabled. Get a Kerberos ticket" >&2
    echo "  (kinit) and re-run sync-mcp.sh." >&2
  fi
  unset _mikey_getkey
fi

# Strip any trailing whitespace/newline so it's safe in a header value.
MIKEY_TOKEN="${MIKEY_TOKEN%"${MIKEY_TOKEN##*[![:space:]]}"}"
export MIKEY_TOKEN

unset _mikey_personal_file _mikey_cvmfs_getkey _mikey_local_getkey
