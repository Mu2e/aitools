#!/usr/bin/env bash
# Harness front end for the CVMFS claudebox (Claude Code in a locked-down
# apptainer container). env.sh defines a `claudebox` function that calls
# this, so with the harness loaded `claudebox ...` gets the Mu2e defaults;
# `command claudebox` or the full CVMFS path bypasses them.
#
# For run / agent / shell it adds, before your own options (so yours win):
#   --claude   the harness's native Claude Code binary
#   --secret   the litellm model mapping (ANTHROPIC_DEFAULT_*_MODEL), so
#              /model names resolve on litellm inside the box too
#   --secret MIKEY_TOKEN  the Mu2e MCP key, from getMu2eKey
#   --mcp      the Mu2e MCP servers from the registry, written with a
#              literal ${MIKEY_TOKEN} that Claude Code expands inside
# and it seeds the box home with the Mu2e context (CLAUDE.md) and a Sonnet
# default. ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL (from the harness .env)
# are forwarded by claudebox itself. After an interactive `run`, the
# session is added to the harness usage log.
#
# Bare `claudebox` (or `claudebox DIR`, `claudebox --opt`) means `run`.
# Other subcommands (list, log, stop, clean, info, help) pass through.

set -uo pipefail

cbx=/cvmfs/mu2e.opensciencegrid.org/bin/claudebox
[[ -x "$cbx" ]] || { echo "claudebox: $cbx not found (is CVMFS mounted?)" >&2; exit 1; }

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
shared="$script_dir/../shared"

sub="${1:-}"
case "$sub" in
  run|agent|start|shell|sh) shift ;;
  -h|--help|help) exec "$cbx" "$@" ;;
  # Bare `claudebox`, `claudebox DIR` or `claudebox --opt ...`: an
  # interactive session, like `claude` (the CVMFS one prints help).
  ""|-*) sub=run ;;
  *) if [[ -d "$sub" ]]; then sub=run; else exec "$cbx" "$@"; fi ;;
esac

# Same root claudebox will use: --root, $CLAUDEBOX_ROOT, else the /exp area.
root="${CLAUDEBOX_ROOT:-/exp/${EXPERIMENT:-mu2e}/app/users/$USER/claudebox}"
prev=""
for a in "$@"; do
  [[ "$prev" == "--root" ]] && root="$a"
  [[ "$a" == --root=* ]] && root="${a#--root=}"
  [[ "$a" == "--" ]] && break
  prev="$a"
done
box_home="$root/home"
mkdir -p "$box_home/.claude" && chmod 700 "$box_home" "$box_home/.claude"

# Box settings: only if the box has none yet -- otherwise claudebox would
# copy your personal ~/.claude/settings.json. No hooks: the harness paths
# are not mounted inside.
if [[ ! -e "$box_home/.claude/settings.json" ]]; then
  ( umask 077; printf '{\n  "model": "sonnet"\n}\n' > "$box_home/.claude/settings.json" )
fi
# Mu2e context, refreshed every launch (a copy: /exp is not mounted inside).
cp -f "$shared/mu2e.md" "$box_home/.claude/CLAUDE.md" 2>/dev/null || true

opts=()
if [[ -n "${CLAUDE_HOME:-}" && -x "$CLAUDE_HOME/.local/bin/claude" ]]; then
  opts+=(--claude "$CLAUDE_HOME/.local/bin/claude")
fi

for v in ANTHROPIC_DEFAULT_OPUS_MODEL ANTHROPIC_DEFAULT_SONNET_MODEL \
         ANTHROPIC_DEFAULT_HAIKU_MODEL ANTHROPIC_DEFAULT_FABLE_MODEL; do
  [[ -n "${!v:-}" ]] && opts+=(--secret "$v")
done

# MCP servers + key. Skipped (with a note) if either is unavailable.
mcp_file="$root/harness-mcp.json"
# shellcheck source=../shared/mikey-token.sh
source "$shared/mikey-token.sh" 2>/dev/null
if [[ -n "${MIKEY_TOKEN:-}" ]]; then
  tmp="$(mktemp)"
  if curl -fsS --max-time 10 http://mu2eaigpvm01.fnal.gov:8000/registry -o "$tmp" 2>/dev/null \
     && ( umask 077; printf '{}\n' > "$mcp_file.new" ) \
     && MIKEY_TOKEN='${MIKEY_TOKEN}' MCP_HEADERS_HELPER= \
        python3 -I "$script_dir/sync_mcp_claude.py" "$tmp" "$mcp_file.new" >/dev/null; then
    mv -f "$mcp_file.new" "$mcp_file"
  else
    rm -f "$mcp_file.new"
    [[ -f "$mcp_file" ]] && echo "claudebox: registry unreachable, using the last MCP list" >&2
  fi
  rm -f "$tmp"
  if [[ -f "$mcp_file" ]]; then
    export MIKEY_TOKEN
    opts+=(--secret MIKEY_TOKEN --mcp "$mcp_file")
  fi
else
  echo "claudebox: no MIKEY token -- starting without the Mu2e MCP servers" >&2
fi

if [[ "$sub" != run ]]; then
  exec "$cbx" "$sub" "${opts[@]}" "$@"
fi

# Interactive run: wait, then log the session like a harness session.
marker="$(mktemp)"
"$cbx" run "${opts[@]}" "$@"
rc=$?
newest="$(find "$box_home/.claude/projects" -name '*.jsonl' -newer "$marker" 2>/dev/null \
          | xargs -r ls -t 2>/dev/null | head -1)"
rm -f "$marker"
if [[ -n "$newest" ]]; then
  LOG_PROJECTS_ROOT="$box_home/.claude/projects" \
    python3 "$script_dir/log-session-claude.py" "$(basename "$newest" .jsonl)" >/dev/null 2>&1 || true
fi
exit $rc
