# Sourced helper (not a standalone script): resolves a litellm.fnal.gov
# API key, preferring in order (1) an already-set $LITELLM_KEY -- a
# caller reusing it across multiple calls in one run, e.g.
# goose-model.sh's list-then-set -- (2) a previously-saved key in
# secrets.yaml (from a prior successful `set`), (3) an interactive
# silent prompt. Every key is checked against litellm.fnal.gov; one it
# rejects (expired, revoked, mistyped) is reported and you're asked for
# another, until one works or you enter nothing. Leaves the result in
# LITELLM_KEY, or exits 1 if none could be obtained.
#
# Requires the caller to have already sourced env.sh (for
# XDG_CONFIG_HOME).

if [[ -z "${LITELLM_KEY:-}" ]]; then
  litellm_secrets_file="$XDG_CONFIG_HOME/goose/secrets.yaml"
  if [[ -f "$litellm_secrets_file" ]]; then
    LITELLM_KEY="$(python3 -c "
import yaml
d = yaml.safe_load(open('$litellm_secrets_file')) or {}
print(d.get('LITELLM_API_KEY', ''))
" 2>/dev/null)"
    [[ -n "$LITELLM_KEY" ]] && litellm_from="saved"
  fi
  unset litellm_secrets_file
fi

while :; do
  if [[ -z "${LITELLM_KEY:-}" ]]; then
    read -rsp "litellm.fnal.gov API key (from https://litellm.fnal.gov, Enter to give up): " LITELLM_KEY || true
    echo >&2
    litellm_from="entered"
    if [[ -z "$LITELLM_KEY" ]]; then
      echo "Error: no key entered" >&2
      unset litellm_from
      exit 1
    fi
  fi
  # 401 = litellm rejected the key. Any other answer (including no network)
  # keeps it: better to try than to block on a flaky connection.
  litellm_code="$(curl -sS -o /dev/null -w '%{http_code}' -m 10 \
    https://litellm.fnal.gov/v1/models -H "Authorization: Bearer ${LITELLM_KEY}" 2>/dev/null)"
  if [[ "$litellm_code" != "401" ]]; then
    [[ "${litellm_from:-}" == "saved" ]] && echo "Using previously-saved litellm.fnal.gov API key." >&2
    [[ "${litellm_from:-}" == "entered" ]] && echo "Key accepted." >&2
    break
  fi
  case "${litellm_from:-}" in
    saved)   echo "The saved litellm.fnal.gov key was rejected (expired or revoked)." >&2 ;;
    entered) echo "litellm.fnal.gov rejected that key -- try again." >&2 ;;
    *)       echo "litellm.fnal.gov rejected the key in \$LITELLM_KEY." >&2 ;;
  esac
  LITELLM_KEY=""
done
unset litellm_code litellm_from
