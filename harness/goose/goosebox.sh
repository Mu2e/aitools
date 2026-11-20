#!/usr/bin/env bash
# Harness front end for goosebox (goose in a locked-down apptainer container;
# the script itself is ../claudebox/goosebox, the agentbox example). env.sh
# defines a `goosebox` function that calls this, so with the harness loaded
# `goosebox ...` gets the Mu2e defaults; running the goosebox script directly
# bypasses them. The counterpart of ../claude-code/claudebox.sh.
#
# For run / agent / shell it adds, before your own options (so yours win):
#   --secret MIKEY_TOKEN  the Mu2e MCP key, from getMu2eKey
#   --mcp      the Mu2e MCP servers from the registry, written with a literal
#              ${MIKEY_TOKEN} that the box script fills in for goose
#   --secret GOOSE_MOIM_MESSAGE_FILE  the Mu2e context plus a note on what
#              this box sees, read by goose's Top Of Mind extension
# The goose binary, provider, model and provider key are found by the box
# script itself (from $GOOSE_BIN_DIR and the harness's goose config, which
# env.sh points at). When a session or an agent ends, it is added to the
# harness usage log (../claudebox/log-usage.sh, through $AGENTBOX_POST_HOOK).
#
# Bare `goosebox` (or `goosebox DIR`, `goosebox --opt`) means `run`.
# Other subcommands (list, log, stop, clean, info, help) pass through.

set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
shared="$script_dir/../shared"

gbx="${GOOSEBOX_BIN:-$script_dir/../claudebox/goosebox}"
[[ -x "$gbx" ]] || { echo "goosebox: $gbx not found" >&2; exit 1; }

sub="${1:-}"
case "$sub" in
  run|agent|start|shell|sh) shift ;;
  -h|--help|help|-v|--version) exec "$gbx" "$@" ;;
  ""|-*) sub=run ;;
  *) if [[ -d "$sub" ]]; then sub=run; else exec "$gbx" "$@"; fi ;;
esac

# Same root the box script will use: --root, $CLAUDEBOX_ROOT, else the /exp area.
root="${CLAUDEBOX_ROOT:-/exp/${EXPERIMENT:-mu2e}/app/users/$USER/claudebox}"
prev=""
for a in "$@"; do
  [[ "$prev" == "--root" ]] && root="$a"
  [[ "$a" == --root=* ]] && root="${a#--root=}"
  [[ "$a" == "--" ]] && break
  prev="$a"
done
box_home="$root/home"
box_cfg="$box_home/.config/goose"
( umask 077; mkdir -p "$box_cfg" ) && chmod 700 "$box_home"

opts=()

# MCP servers + key. Skipped (with a note) if either is unavailable. The file
# is the one the claudebox wrapper writes too: Claude's format, which the box
# script turns into goose extensions.
mcp_file="$root/harness-mcp.json"
# shellcheck source=../shared/mikey-token.sh
source "$shared/mikey-token.sh" 2>/dev/null
if [[ -n "${MIKEY_TOKEN:-}" ]]; then
  tmp="$(mktemp)"
  if curl -fsS --max-time 10 http://mu2eaigpvm01.fnal.gov:8000/registry -o "$tmp" 2>/dev/null \
     && ( umask 077; printf '{}\n' > "$mcp_file.new" ) \
     && MIKEY_TOKEN='${MIKEY_TOKEN}' MCP_HEADERS_HELPER= \
        python3 -I "$script_dir/../claude-code/sync_mcp_claude.py" "$tmp" "$mcp_file.new" >/dev/null; then
    mv -f "$mcp_file.new" "$mcp_file"
  else
    rm -f "$mcp_file.new"
    [[ -f "$mcp_file" ]] && echo "goosebox: registry unreachable, using the last MCP list" >&2
  fi
  rm -f "$tmp"
  if [[ -f "$mcp_file" ]]; then
    export MIKEY_TOKEN
    opts+=(--secret MIKEY_TOKEN --mcp "$mcp_file")
  fi
else
  echo "goosebox: no MIKEY token -- starting without the Mu2e MCP servers" >&2
fi

# The options are read here only to describe the session; the box script
# does the real parsing.
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
    --secret|--secret-file|--mcp|--permission-mode|--model|--budget|--timeout|--prompt-file|--root|--image|--bin|--tool|-f) ((i++)) ;;
    -*) ;;
    *) [[ -z "$workdir" && "$sub" != agent && -d "$a" ]] && workdir="$a" ;;
  esac
  ((i++))
done
if [[ -z "$workdir" ]]; then
  [[ "$sub" == agent ]] && workdir="$root/work" || workdir="$PWD"
fi
workdir="$(cd "$workdir" 2>/dev/null && pwd -P || echo "$workdir")"

# The box script lets the whole /exp/<exp>/app/users/$USER area through (its
# depth check is one level short); that includes the harness config and keys.
if [[ "$workdir" == /exp/* ]] && [[ "$(awk -F/ '{print NF-1}' <<<"$workdir")" -lt 6 ]]; then
  echo "goosebox: refusing $workdir as the work directory: it holds your harness" >&2
  echo "  config and keys. Use a project directory below it (-C DIR)." >&2
  exit 1
fi

# Mu2e context plus a note on what this particular box sees, refreshed every
# launch, in the box home (a copy: /exp is not mounted inside). goose's Top
# Of Mind extension reads it from the path in GOOSE_MOIM_MESSAGE_FILE, which
# is the path as seen inside the box.
context="$box_cfg/mu2e-context.md"
if {
  cat "$shared/mu2e.md" "$script_dir/../claudebox/box-note.md"
  echo
  echo "## This session"
  echo
  echo "- Work directory: \`$workdir\` (read-write, at its host path; the rest of \`/exp\` does not exist)"
  echo "- \`/cvmfs\`: read-only"
  if [[ "$pnfs" == 1 ]]; then echo "- \`/pnfs\`: read-only"; else echo "- \`/pnfs\`: not available"; fi
  for b in ${binds[@]+"${binds[@]}"}; do echo "- Extra bind \`$b\` (read-only unless it ends in \`:rw\`)"; done
  if [[ -n "${MIKEY_TOKEN:-}" && -f "$mcp_file" ]]; then
    echo "- Mu2e MCP servers: configured as extensions (tell the user if one is missing)"
  else
    echo "- Mu2e MCP servers: NOT available in this session; do not offer the runs/dqm/ecl/metacat/kb tools"
  fi
  (( ${#imports[@]} )) && echo "- Credential imports requested: ${imports[*]}"
  true
} > "$context" 2>/dev/null; then
  export GOOSE_MOIM_MESSAGE_FILE="/home/box/.config/goose/mu2e-context.md"
  opts+=(--secret GOOSE_MOIM_MESSAGE_FILE)
fi

# Usage logging, on the host, when the session or the agent ends.
hook="$script_dir/../claudebox/log-usage.sh"
[[ -x "$hook" ]] && export AGENTBOX_POST_HOOK="${AGENTBOX_POST_HOOK:-$hook}"

exec "$gbx" "$sub" ${opts[@]+"${opts[@]}"} "$@"
