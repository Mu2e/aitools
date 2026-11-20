#!/usr/bin/env bash
# Usage:
#   . setup.sh [-v] [version]     (sourced -- also leaves goose on PATH in your shell)
#   ./setup.sh [-v] [version]     (executed -- installs only, doesn't touch your shell)
#
# Idempotent: installs goose only if it isn't already present at
# $GOOSE_BIN_DIR/goose (see env.sh, which this always sources). Safe to
# re-run either way.
#
#   version   Passed through to install.sh if an install is actually needed.
#   -v, --verbose   Show every step (paths, MCP server sync details); the
#             default is one summary line plus anything that needs attention.
#
# To force a reinstall/upgrade even though goose is already present, run
# install.sh directly instead.
#
# Deliberately does NOT `set -e`/`set -u` at this file's top level: this
# script is meant to be sourceable, and those options would otherwise
# leak into the caller's interactive shell and can silently kill it (see
# install.sh's comment on this same issue) -- so errors are checked
# explicitly below instead. install.sh keeps its own `set -euo pipefail`
# safely, because it's always run here as a plain subprocess (`bash
# install.sh`), never sourced.

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat >&2 <<'USAGE'
Usage:
  . setup.sh [-v] [version]
  ./setup.sh [-v] [version]

  -v, --verbose   show every step (paths, MCP server sync details)
USAGE
  return 2 2>/dev/null || exit 2
fi

_verbose=0
_version=""
for _arg in "$@"; do
  case "$_arg" in
    -v|--verbose) _verbose=1 ;;
    *) _version="$_arg" ;;
  esac
done
unset _arg

# Output: one summary line unless something needs attention. _detail lines
# show only with -v; warnings are red on a terminal (not with $NO_COLOR).
_detail() { [[ "$_verbose" == 1 ]] && echo "$@"; return 0; }
_warn() {
  if [[ -t 2 && -z "${NO_COLOR:-}" ]]; then
    printf '\033[1;31m%s\033[0m\n' "$@" >&2
  else
    printf '%s\n' "$@" >&2
  fi
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -z "$script_dir" ]]; then
  echo "Error: could not resolve script directory" >&2
  return 1 2>/dev/null || exit 1
fi

if ! source "$script_dir/env.sh"; then
  echo "Error: failed to source env.sh" >&2
  unset script_dir
  return 1 2>/dev/null || exit 1
fi

config_file="$XDG_CONFIG_HOME/goose/config.yaml"
if [[ ! -e "$config_file" && -f "$script_dir/config.yaml.example" ]]; then
  _detail "No goose config yet -- seeding from harness template: $config_file"
  mkdir -p "$(dirname "$config_file")"
  if ! ( umask 077 && cp "$script_dir/config.yaml.example" "$config_file" ); then
    echo "Error: failed to seed $config_file" >&2
    unset script_dir config_file
    return 1 2>/dev/null || exit 1
  fi
fi
unset config_file

if [[ -x "$GOOSE_BIN_DIR/goose" ]]; then
  _detail "goose already installed: $GOOSE_BIN_DIR/goose"
else
  echo "goose not found at $GOOSE_BIN_DIR/goose -- installing"
  if ! bash "$script_dir/install.sh" ${_version:+"$_version"}; then
    echo "Error: install.sh failed" >&2
    unset script_dir
    return 1 2>/dev/null || exit 1
  fi
fi

# Non-fatal: no network / registry down shouldn't block getting goose
# itself usable. Gated servers just come in disabled if there's no mikey
# token yet (see sync_mcp.py) -- rerun sync-mcp.sh by hand once you have one.
_detail "Syncing MCP servers from the registry..."
_mcp_summary="MCP servers not synced"
if _sync_out="$(bash "$script_dir/sync-mcp.sh" 2>&1)"; then
  if [[ "$_verbose" == 1 ]]; then
    printf '%s\n' "$_sync_out"
  else
    # Quiet: only what needs attention (a warning/note and its indented
    # continuation lines), e.g. no MIKEY token.
    _sync_notes="$(printf '%s\n' "$_sync_out" | awk '
      /^(Warning|Note|Error|No token)/ {keep=1; print; next}
      keep && /^[ \t]/ {print; next}
      {keep=0}')"
    [[ -n "$_sync_notes" ]] && _warn "$_sync_notes"
  fi
  _mcp_count="$(printf '%s\n' "$_sync_out" | sed -n 's/^\(Added\|Updated\): //p' | tr ',' '\n' | grep -c .)"
  _mcp_summary="$_mcp_count MCP servers"
else
  printf '%s\n' "$_sync_out" >&2
  _warn "Warning: MCP servers weren't synced (goose itself is installed)." \
        "  Once the registry is reachable: $script_dir/sync-mcp.sh"
fi

_goose_version="$("$GOOSE_BIN_DIR/goose" --version 2>/dev/null | awk 'NR==1{print $NF}')"
echo "goose ${_goose_version:-(version unknown)} ready, $_mcp_summary. Run: goose session"
echo "In a new shell, first: . $script_dir/env.sh"

unset script_dir _verbose _version _goose_version _sync_out _sync_notes _mcp_count _mcp_summary
unset -f _detail _warn
