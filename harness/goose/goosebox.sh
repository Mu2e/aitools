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

# goose's box has a root of its own, not claudebox's: the box home keeps
# what a session leaves there (Claude's copied login among it), and one
# tool's sessions should not be able to read the other's. $GOOSEBOX_ROOT, or
# --root, selects another. Exported under the name the box script reads, and
# before the subcommands that pass straight through (list, log, stop, ...).
export CLAUDEBOX_ROOT="${GOOSEBOX_ROOT:-/exp/${EXPERIMENT:-mu2e}/app/users/$USER/goosebox}"

sub="${1:-}"
case "$sub" in
  run|agent|start|shell|sh) shift ;;
  -h|--help|help|-v|--version) exec "$gbx" "$@" ;;
  ""|-*) sub=run ;;
  *) if [[ -d "$sub" ]]; then sub=run; else exec "$gbx" "$@"; fi ;;
esac
[[ "$sub" == start ]] && sub=agent    # an alias: an agent's default work directory, not $PWD

# Same root the box script will use: --root, else the one set above.
root="$CLAUDEBOX_ROOT"
prev=""
for a in "$@"; do
  [[ "$prev" == "--root" ]] && root="$a"
  [[ "$a" == --root=* ]] && root="${a#--root=}"
  [[ "$a" == "--" ]] && break
  prev="$a"
done
box_home="$root/home"
box_cfg="$box_home/.config/goose"
# The box home is writable by whatever ran in the box before. A link left
# there, where a folder or file is expected, must not send what is written
# here on the host to somewhere else: links are removed, files replaced.
for d in "$box_home/.config" "$box_cfg" "$box_home/.agents" "$box_home/.agents/skills"; do
  [[ -L "$d" ]] && rm -f "$d"
done
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

# The work directory is writable in the box, at its host path. Refuse one
# that holds, or lies inside, a folder with your keys, the harness's own
# programs or the box's staged secrets: the harness homes, the hep-genesis
# install and data, and the box root (its work/ folder excepted). The box
# script's own check only counts the depth of the path.
if [[ "$workdir" == /exp/* ]] && [[ "$(awk -F/ '{print NF-1}' <<<"$workdir")" -lt 6 ]]; then
  echo "goosebox: refusing $workdir as the work directory: it holds your harness" >&2
  echo "  config and keys. Use a project directory below it (-C DIR)." >&2
  exit 1
fi
_users="/exp/${EXPERIMENT:-mu2e}"
root_real="$(cd "$root" 2>/dev/null && pwd -P || echo "$root")"
for p in "${CLAUDE_HOME:+$(dirname "$CLAUDE_HOME")}" "${GOOSE_ROOT:-}" \
         "$_users/app/users/$USER/claude-code" "$_users/app/users/$USER/goose" \
         "$_users/app/users/$USER/claudebox" \
         "$_users/app/users/$USER/hep-genesis" "${HG_DATA_DIR:-$_users/data/users/$USER/hep-genesis-data}" \
         "$root_real"; do
  [[ -n "$p" && -d "$p" ]] || continue
  p="$(cd "$p" 2>/dev/null && pwd -P)" || continue
  [[ "$p" == "$root_real" && "$workdir/" == "$root_real/work/"* ]] && continue
  if [[ "$workdir/" == "$p/"* || "$p/" == "$workdir/"* ]]; then
    echo "goosebox: refusing $workdir as the work directory: it overlaps $p," >&2
    echo "  which holds keys, settings or programs the box must not be able to change." >&2
    echo "  Use a project directory elsewhere (-C DIR)." >&2
    exit 1
  fi
done

# Mu2e context plus a note on what this particular box sees, refreshed every
# launch, in the box home (a copy: /exp is not mounted inside). goose's Top
# Of Mind extension reads it from the path in GOOSE_MOIM_MESSAGE_FILE, which
# is the path as seen inside the box.
context="$box_cfg/mu2e-context.md"
rm -f "$context"                         # replaced, never written through
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

# Skills, as copies: the sources (/exp, this clone) are not mounted inside.
# goose reads ~/.agents/skills, and the box's home is $box_home.
# $MU2E_BOX_SKILLS limits them: a list ("a,b"), or "none".
python3 "$shared/link-skills.py" "$box_home/.agents/skills" --copy \
    ${MU2E_BOX_SKILLS:+--only "$MU2E_BOX_SKILLS"} >/dev/null \
  || echo "goosebox: the skills were not copied into the box" >&2

# Usage logging, on the host, when the session or the agent ends.
hook="$script_dir/../claudebox/log-usage.sh"
[[ -x "$hook" ]] && export AGENTBOX_POST_HOOK="${AGENTBOX_POST_HOOK:-$hook}"

exec "$gbx" "$sub" ${opts[@]+"${opts[@]}"} "$@"
