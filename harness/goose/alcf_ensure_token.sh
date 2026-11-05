# Sourced helper (not a standalone script): ensures the ALCF auth venv
# and a usable access token exist, printing clear status/guidance either
# way, and sets ALCF_VENV / ALCF_TOKEN for the caller. Used by both
# alcf.sh and goose-model.sh so "check token, guide through auth if
# needed" lives in exactly one place instead of being duplicated.
#
# Requires the caller to have already set `script_dir` to its own
# directory (both alcf.sh and goose-model.sh do this near the top).

alcf_root="${GOOSE_ROOT:-/exp/mu2e/app/users/$USER/goose}/alcf"
ALCF_VENV="$alcf_root/venv"
alcf_auth_script="$script_dir/alcf_auth.py"
mkdir -p "$alcf_root"

if [[ ! -x "$ALCF_VENV/bin/python" ]]; then
  echo "Setting up ALCF auth environment (one-time)..." >&2
  /usr/bin/python3 -m venv "$ALCF_VENV"
  "$ALCF_VENV/bin/python" -m pip install --quiet globus_sdk
fi

ALCF_TOKEN="$("$ALCF_VENV/bin/python" "$alcf_auth_script" get_access_token 2>/dev/null || true)"
if [[ -z "$ALCF_TOKEN" ]]; then
  echo "No active ALCF token found -- let's get you authenticated." >&2
  "$ALCF_VENV/bin/python" "$alcf_auth_script" authenticate
  ALCF_TOKEN="$("$ALCF_VENV/bin/python" "$alcf_auth_script" get_access_token)"
  if [[ -z "$ALCF_TOKEN" ]]; then
    echo "Error: still no ALCF token after authenticating" >&2
    exit 1
  fi
  echo "ALCF authentication succeeded." >&2
else
  echo "ALCF token is active." >&2
fi

# A token that refreshes fine can still be refused: ALCF wants a fresh
# login with your identity provider every so often ("high-assurance
# timeout"), which a refresh doesn't satisfy. Check, and re-login once.
_alcf_check() {
  curl -sS -o /dev/null -w '%{http_code}' -m 15 \
    https://inference-api.alcf.anl.gov/resource_server/list-endpoints \
    -H "Authorization: Bearer ${ALCF_TOKEN}" 2>/dev/null
}
if [[ "$(_alcf_check)" == "401" ]]; then
  echo "ALCF refused the token: it needs a fresh login (high-assurance timeout)." >&2
  echo "First log out of Globus in your browser: https://app.globus.org/logout" >&2
  echo "then log in again below with fnal.gov or anl.gov." >&2
  read -rp "Press Enter when logged out (Ctrl-C to cancel)... " _ || true
  "$ALCF_VENV/bin/python" "$alcf_auth_script" authenticate
  ALCF_TOKEN="$("$ALCF_VENV/bin/python" "$alcf_auth_script" get_access_token 2>/dev/null || true)"
  if [[ -z "$ALCF_TOKEN" || "$(_alcf_check)" == "401" ]]; then
    echo "Error: ALCF still refuses the token -- make sure you logged out of Globus" >&2
    echo "first and logged in with an authorized identity (fnal.gov, anl.gov, ...)." >&2
    unset -f _alcf_check
    exit 1
  fi
  echo "ALCF authentication succeeded." >&2
fi
unset -f _alcf_check

unset alcf_root alcf_auth_script
