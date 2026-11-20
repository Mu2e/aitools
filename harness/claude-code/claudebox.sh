#!/usr/bin/env bash
# Harness front end for claudebox (Claude Code in a locked-down apptainer
# container; the script itself is ../claudebox/claudebox). env.sh defines a
# `claudebox` function that calls this, so with the harness loaded
# `claudebox ...` gets the Mu2e defaults; running the claudebox script
# directly bypasses them.
#
# For run / agent / shell it adds, before your own options (so yours win):
#   --claude   the harness's native Claude Code binary
#   --secret   the litellm model mapping (ANTHROPIC_DEFAULT_*_MODEL), so
#              /model names resolve on litellm inside the box too
#   --secret MIKEY_TOKEN  the Mu2e MCP key, from getMu2eKey
#   --mcp      the Mu2e MCP servers from the registry, written with a
#              literal ${MIKEY_TOKEN} that Claude Code expands inside
# and it seeds the box home with the Mu2e context (CLAUDE.md) and a Sonnet
# default. ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL (from the harness .env,
# loaded below) are forwarded by claudebox itself. After an interactive `run`, the
# session is added to the harness usage log. (A claudebox that knows
# $AGENTBOX_POST_HOOK -- the agentbox example -- logs through that instead,
# which also covers agents: see ../claudebox/log-usage.sh.)
#
# Bare `claudebox` (or `claudebox DIR`, `claudebox --opt`) means `run`.
# Other subcommands (list, log, stop, clean, info, help) pass through.

set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
shared="$script_dir/../shared"

# The claudebox script: the copy that comes with the harness, else the one
# in the CVMFS bin directory. $CLAUDEBOX_BIN overrides both.
cbx="${CLAUDEBOX_BIN:-$script_dir/../claudebox/claudebox}"
[[ -x "$cbx" || -n "${CLAUDEBOX_BIN:-}" ]] || cbx=/cvmfs/mu2e.opensciencegrid.org/bin/claudebox
[[ -x "$cbx" ]] || { echo "claudebox: $cbx not found" >&2; exit 1; }

# Backend settings (key, base URL, model mapping). env.sh keeps them out of
# the user's shell, so load them here.
# shellcheck source=/dev/null
[[ -n "${CLAUDE_HOME:-}" && -f "$CLAUDE_HOME/.env" ]] && source "$CLAUDE_HOME/.env"

# The box script copies the login, the account details and the settings from
# $HOME. Give it the harness's home, as the `claude` function does: otherwise
# it takes whatever login is in your real home, which may be an old one that
# has expired while the harness's is fine.
[[ -n "${CLAUDE_HOME:-}" && -d "$CLAUDE_HOME/.claude" ]] && export HOME="$CLAUDE_HOME"

sub="${1:-}"
case "$sub" in
  run|agent|start|shell|sh) shift ;;
  -h|--help|help|-v|--version) exec "$cbx" "$@" ;;
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

# Mu2e context plus a note on what this particular box sees, refreshed every
# launch (a copy: /exp is not mounted inside). The options are read here only
# to describe them; claudebox does the real parsing.
workdir=""; pnfs=0; binds=(); imports=(); sub_args=("$@")
i=0
while (( i < ${#sub_args[@]} )); do
  a="${sub_args[i]}"
  case "$a" in
    --) break ;;
    -C|--workdir) workdir="${sub_args[i+1]:-}"; ((i++)) ;;
    --workdir=*) workdir="${a#--workdir=}" ;;
    -b|--bind) binds+=("${sub_args[i+1]:-}"); ((i++)) ;;
    --pnfs) pnfs=1 ;;
    --krb5|--kerberos|--krb5-ccache|--token|--scitoken|--token-file|--github|--gh|--mikey|--display)
      imports+=("${a#--}")
      [[ "$a" == --krb5-ccache || "$a" == --token-file ]] && ((i++)) ;;
    --secret|--secret-file|--mcp|--permission-mode|--model|--budget|--timeout|--prompt-file|--root|--image|--claude|-f) ((i++)) ;;
    -*) ;;
    *) [[ -z "$workdir" && "$sub" != agent && -d "$a" ]] && workdir="$a" ;;
  esac
  ((i++))
done
if [[ -z "$workdir" ]]; then
  [[ "$sub" == agent ]] && workdir="$root/work" || workdir="$PWD"
fi
workdir="$(cd "$workdir" 2>/dev/null && pwd -P || echo "$workdir")"

# claudebox lets the whole /exp/<exp>/app/users/$USER area through (its depth
# check is one level short); that includes the harness config and API key.
if [[ "$workdir" == /exp/* ]] && [[ "$(awk -F/ '{print NF-1}' <<<"$workdir")" -lt 6 ]]; then
  echo "claudebox: refusing $workdir as the work directory: it holds your harness" >&2
  echo "  config and keys. Use a project directory below it (-C DIR)." >&2
  exit 1
fi

{
  cat "$shared/mu2e.md" "$script_dir/../claudebox/box-note.md"
  echo
  echo "## This session"
  echo
  echo "- Work directory: \`$workdir\` (read-write, at its host path; the rest of \`/exp\` does not exist)"
  echo "- \`/cvmfs\`: read-only"
  if [[ "$pnfs" == 1 ]]; then echo "- \`/pnfs\`: read-only"; else echo "- \`/pnfs\`: not available"; fi
  for b in "${binds[@]}"; do echo "- Extra bind \`$b\` (read-only unless it ends in \`:rw\`)"; done
  if [[ -n "${MIKEY_TOKEN:-}" && -f "$mcp_file" ]]; then
    echo "- Mu2e MCP servers: configured (check \`/mcp\`; tell the user if one is missing)"
  else
    echo "- Mu2e MCP servers: NOT available in this session; do not offer the runs/dqm/ecl/metacat/kb tools"
  fi
  (( ${#imports[@]} )) && echo "- Credential imports requested: ${imports[*]}"
} > "$box_home/.claude/CLAUDE.md" 2>/dev/null || true

# Skills, as copies: the sources (/exp, this clone) are not mounted inside.
# $MU2E_BOX_SKILLS limits them: a list ("a,b"), or "none".
python3 "$shared/link-skills.py" "$box_home/.claude/skills" --copy \
    ${MU2E_BOX_SKILLS:+--only "$MU2E_BOX_SKILLS"} >/dev/null \
  || echo "claudebox: the skills were not copied into the box" >&2

# Usage logging by the box script itself, where it supports that.
hook="$script_dir/../claudebox/log-usage.sh"
if [[ -x "$hook" ]] && grep -q AGENTBOX_POST_HOOK "$cbx" 2>/dev/null; then
  export AGENTBOX_POST_HOOK="${AGENTBOX_POST_HOOK:-$hook}"
  exec "$cbx" "$sub" "${opts[@]}" "$@"
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
