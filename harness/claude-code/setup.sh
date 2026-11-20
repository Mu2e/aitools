#!/usr/bin/env bash
# Usage:
#   . setup.sh [--refresh-settings] [--reset-login] [VERSION]   (sourced -- also leaves claude on PATH in your shell)
#   ./setup.sh [--refresh-settings] [--reset-login] [VERSION]   (executed -- installs only, doesn't touch your shell)
#
# VERSION is optional: stable, latest, or e.g. 2.1.318 (default: latest).
# Only used for a first install; afterwards Claude Code updates itself.
#
# --refresh-settings replaces your settings.json with the current harness
# template (hooks, deny rules, default model); the old file is kept in
# $CLAUDE_HOME/.claude/backups/. Without it an existing settings.json is
# never touched, so use this after a harness update that changed the
# template, or after moving the aitools clone.
#
# --reset-login forgets the configured backend (API key in
# $CLAUDE_HOME/.env, claude.ai login in .claude/.credentials.json; both are
# moved to $CLAUDE_HOME/.claude/backups/) and shows the backend menu again,
# e.g. to switch between litellm, an Anthropic API key and a subscription.
# Settings are independent: --refresh-settings doesn't touch the backend.
#
# Idempotent: safe to re-run. Installs the native Claude Code build (no
# Node.js needed) into $CLAUDE_HOME if not already present, seeds config
# files, and syncs MCP servers from the registry.
#
# Deliberately does NOT use `set -e` at top-level: this script is meant to
# be sourceable (`. setup.sh`), and set -e leaking into an interactive shell
# can silently break it on any failing command. Errors are checked explicitly
# instead.

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat >&2 <<'USAGE'
Usage:
  . setup.sh [--refresh-settings] [--reset-login] [-v] [stable|latest|VERSION]
  ./setup.sh [--refresh-settings] [--reset-login] [-v] [stable|latest|VERSION]

  --refresh-settings   replace settings.json with the current harness
                       template (the old one is kept in .claude/backups/)
  --reset-login        forget the API key / claude.ai login and choose a
                       backend again (old files are kept in .claude/backups/)
  -v, --verbose        show every step (paths, MCP server sync details)
USAGE
  return 2 2>/dev/null || exit 2
fi

_refresh_settings=0
_reset_login=0
_verbose=0
_version=""
for _arg in "$@"; do
  case "$_arg" in
    --refresh-settings) _refresh_settings=1 ;;
    --reset-login) _reset_login=1 ;;
    -v|--verbose) _verbose=1 ;;
    -*) echo "Error: unknown option '$_arg' (see . setup.sh --help)" >&2
        unset _refresh_settings _reset_login _verbose _version _arg
        return 2 2>/dev/null || exit 2 ;;
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

# Always source env.sh first -- it defines CLAUDE_HOME, CLAUDE_BIN_DIR, etc.
if ! source "$script_dir/env.sh"; then
  echo "Error: failed to source env.sh" >&2
  unset script_dir
  return 1 2>/dev/null || exit 1
fi

# ---------------------------------------------------------------------------
# 1. Find or install Claude Code (native build)
#
# Anthropic's native installer puts a self-contained binary (no Node.js)
# under $HOME/.local/share/claude/versions/ with a launcher symlink at
# $HOME/.local/bin/claude, and Claude Code then auto-updates that symlink.
# We run it with HOME=$CLAUDE_HOME so all of that (~320 MB) lands in group
# storage, not /nashome. It doesn't edit any shell rc files.
# ---------------------------------------------------------------------------

claude_bin="$CLAUDE_HOME/.local/bin/claude"
claude_installer_url="https://claude.ai/install.sh"

if [[ ! -x "$claude_bin" ]]; then
  echo "Claude Code not found in $CLAUDE_HOME -- installing the native build..."
  _installer="$(mktemp)"
  if ! curl -fsSL "$claude_installer_url" -o "$_installer"; then
    echo "Error: could not download $claude_installer_url" >&2
    rm -f "$_installer"
    unset script_dir claude_bin claude_installer_url _installer
    return 1 2>/dev/null || exit 1
  fi
  # Subshell: keep HOME/umask changes out of the caller's shell.
  if ! ( export HOME="$CLAUDE_HOME" PATH="$CLAUDE_HOME/.local/bin:$PATH"; umask 022; bash "$_installer" ${_version:+"$_version"} ); then
    echo "Error: Claude Code installer failed (see output above)" >&2
    rm -f "$_installer"
    unset script_dir claude_bin claude_installer_url _installer
    return 1 2>/dev/null || exit 1
  fi
  rm -f "$_installer"
  unset _installer
fi
unset claude_installer_url

if [[ ! -x "$claude_bin" ]]; then
  echo "Error: $claude_bin missing after install attempt" >&2
  unset script_dir claude_bin
  return 1 2>/dev/null || exit 1
fi

_detail "Claude Code found: $claude_bin"
_claude_version="$(HOME="$CLAUDE_HOME" "$claude_bin" --version 2>/dev/null | awk 'NR==1{print $1}')"

# Write a thin wrapper into CLAUDE_BIN_DIR (first on PATH via env.sh) so
# `claude` always gets the HOME override, KRB5 scrubbing and umask -- also
# from scripts that never sourced env.sh. It execs the launcher symlink, not
# a versioned path, so Claude Code's auto-updates are picked up.
# $CLAUDE_HOME/.local/bin goes on PATH inside the session so Claude Code's
# own "is my launcher on PATH" check is satisfied.
# The backend settings (.env) are loaded here, for claude only, rather than
# exported into the user's shell by env.sh.
mkdir -p "$CLAUDE_BIN_DIR"
cat >| "$CLAUDE_BIN_DIR/claude" <<WRAPPER
#!/usr/bin/env bash
# Generated by aitools/harness/claude-code/setup.sh -- do not edit.
[[ -f "$CLAUDE_HOME/.env" ]] && source "$CLAUDE_HOME/.env"
export HOME="$CLAUDE_HOME"
export PATH="$CLAUDE_HOME/.local/bin:\$PATH"
export KRB5CCNAME="/tmp/.claude-no-krb5-cache-\$\$"
umask 077
exec "$claude_bin" "\$@"
WRAPPER
chmod 0755 "$CLAUDE_BIN_DIR/claude"

# ---------------------------------------------------------------------------
# 2. Set up CLAUDE_HOME directory structure
# ---------------------------------------------------------------------------

mkdir -p "$CLAUDE_HOME/.claude/backups"

# ---------------------------------------------------------------------------
# 3. Seed settings.json from template if not present (or on
#    --refresh-settings, keeping the old file as a backup)
# ---------------------------------------------------------------------------

settings_file="$CLAUDE_HOME/.claude/settings.json"
settings_example="$script_dir/settings.json.example"

if [[ "$_refresh_settings" == 1 && -f "$settings_file" && -f "$settings_example" ]]; then
  _settings_backup="$CLAUDE_HOME/.claude/backups/settings.json.$(date +%Y%m%d-%H%M%S)"
  if mv "$settings_file" "$_settings_backup"; then
    echo "settings.json replaced with the harness template (old one: $_settings_backup)"
  else
    echo "Error: could not back up $settings_file -- leaving it as it is" >&2
  fi
  unset _settings_backup
fi

if [[ ! -f "$settings_file" && -f "$settings_example" ]]; then
  _detail "Seeding Claude Code settings from harness template: $settings_file"
  # Expand ${CLAUDE_HARNESS_DIR} placeholder to the real hooks directory path.
  hooks_dir="$script_dir/hooks"
  if ! ( umask 077
         sed "s|\${CLAUDE_HARNESS_DIR}|$hooks_dir|g" "$settings_example" >| "$settings_file" ); then
    echo "Error: failed to seed $settings_file" >&2
    unset script_dir claude_bin settings_file settings_example hooks_dir
    return 1 2>/dev/null || exit 1
  fi
  unset hooks_dir
fi

# An existing settings.json is left alone -- but say so if it doesn't run
# this harness's guard-rail hook (clone moved, or hooks edited out).
if [[ -f "$settings_file" ]] && ! grep -qF "$script_dir/hooks/block-commands.sh" "$settings_file"; then
  _warn "Warning: your settings.json does not use this harness's guard-rail hook" \
        "  (set up from another copy of aitools?). To fix, keeping a backup:" \
        "  . $script_dir/setup.sh --refresh-settings"
  _detail "  settings: $settings_file"
  _detail "  hook:     $script_dir/hooks/block-commands.sh"
fi

# ---------------------------------------------------------------------------
# 4. Symlink CLAUDE.md (shared context) into CLAUDE_HOME/.claude
#    ~/.claude/CLAUDE.md is Claude Code's user-level memory, loaded in every
#    session wherever you work. (~/CLAUDE.md, where earlier versions put it,
#    is only read for sessions started inside $HOME, i.e. practically never.)
# ---------------------------------------------------------------------------

claude_md_target="$CLAUDE_HOME/.claude/CLAUDE.md"
claude_md_source="$(cd "$script_dir/../shared" && pwd)/mu2e.md"

if [[ -f "$claude_md_source" ]]; then
  if [[ ! -e "$claude_md_target" ]] || [[ "$(readlink "$claude_md_target" 2>/dev/null)" != "$claude_md_source" ]]; then
    ln -sfn "$claude_md_source" "$claude_md_target"
    _detail "Linked CLAUDE.md → $claude_md_source"
  fi
  # Drop the old, ineffective link from earlier versions.
  if [[ "$(readlink "$CLAUDE_HOME/CLAUDE.md" 2>/dev/null)" == "$claude_md_source" ]]; then
    rm -f "$CLAUDE_HOME/CLAUDE.md"
  fi
else
  _warn "Warning: shared/mu2e.md not found at $claude_md_source -- CLAUDE.md not linked"
fi

# ---------------------------------------------------------------------------
# 5. API key / backend configuration
# ---------------------------------------------------------------------------

env_file="$CLAUDE_HOME/.env"

# --reset-login: move the stored key / login aside so the menu below
# shows again. Backups keep the key or login recoverable.
if [[ "$_reset_login" == 1 ]]; then
  _stamp="$(date +%Y%m%d-%H%M%S)"
  for _f in "$env_file" "$CLAUDE_HOME/.claude/.credentials.json"; do
    [[ -e "$_f" ]] || continue
    _bak="$CLAUDE_HOME/.claude/backups/$(basename "$_f").$_stamp"
    if mv "$_f" "$_bak"; then
      echo "Login reset: moved $_f to $_bak"
    else
      echo "Error: could not move $_f aside -- the backend menu may not appear" >&2
    fi
  done
  if [[ -f "$settings_file" ]] && grep -q '"apiKeyHelper"' "$settings_file"; then
    _warn "Note: settings.json sets apiKeyHelper, which still counts as a backend." \
          "  Remove it from $settings_file (or use --refresh-settings) to choose another."
  fi
  unset _stamp _f _bak
fi

_has_backend() {
  # Returns 0 if any usable backend is already configured:
  #   - API key / base URL in env or .env file
  #   - apiKeyHelper in settings.json
  #   - claude.ai OAuth credentials (written by /login inside claude)
  # Not $ANTHROPIC_API_KEY from the shell alone: after reinstalling, it is
  # often a leftover from the previous install's .env (older versions of
  # env.sh exported it into the shell),
  # and nothing would be saved for the next shell. That case is handled
  # below.
  if [[ -f "$env_file" ]]; then
    grep -q "ANTHROPIC_API_KEY\|ANTHROPIC_BASE_URL" "$env_file" 2>/dev/null && return 0
  fi
  if [[ -f "$settings_file" ]]; then
    python3 -c "
import json, sys
d = json.load(open('$settings_file'))
sys.exit(0 if 'apiKeyHelper' in d else 1)
" 2>/dev/null && return 0
  fi
  # claude.ai subscription: `/login` stores its OAuth credentials in
  # ~/.claude/.credentials.json on Linux.
  [[ -s "$CLAUDE_HOME/.claude/.credentials.json" ]] && return 0
  return 1
}

if ! _has_backend && [[ -n "${ANTHROPIC_API_KEY:-}" ]]; then
  echo ""
  echo "Note: ANTHROPIC_API_KEY is set in this shell${ANTHROPIC_BASE_URL:+ (for $ANTHROPIC_BASE_URL)},"
  echo "but not saved in $env_file -- probably left over from an earlier"
  echo "install, and gone in a new shell. Choose a backend below to save one."
  [[ "${ANTHROPIC_BASE_URL:-}" == *litellm.fnal.gov* ]] && _leftover_litellm_key="$ANTHROPIC_API_KEY"
  unset ANTHROPIC_API_KEY ANTHROPIC_BASE_URL
fi

if ! _has_backend; then
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  Claude Code needs a backend. Choose one:"
  echo ""
  echo "  [1] litellm.fnal.gov  (shared Fermilab endpoint; personal API"
  echo "                         key from https://litellm.fnal.gov)"
  echo "  [2] Personal Anthropic API key  (console.anthropic.com)"
  echo "  [3] Skip for now  (API key or claude.ai subscription -- configure"
  echo "                     later: setup-litellm.sh or /login in claude)"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo ""
  read -rp "Choice [1/2/3]: " _choice

  case "${_choice:-1}" in
    1)
      # A litellm key left in the shell is reused (and saved) instead of asked for.
      LITELLM_KEY="${_leftover_litellm_key:-}" bash "$script_dir/setup-litellm.sh"
      ;;
    2)
      echo ""
      echo "Get a key at: https://console.anthropic.com/settings/keys"
      read -rsp "Anthropic API key: " _api_key
      echo ""
      if [[ -z "$_api_key" ]]; then
        echo "No key entered -- skipping. Re-run setup.sh when you have one." >&2
      else
        ( umask 077 && printf 'export ANTHROPIC_API_KEY=%s\n' "$_api_key" >| "$env_file" )
        echo "Key saved to $env_file"
      fi
      unset _api_key
      ;;
    3|*)
      echo "Skipping backend setup. When ready:"
      echo "  $script_dir/setup-litellm.sh"
      echo "                              configure litellm.fnal.gov"
      echo "  . $script_dir/setup.sh"
      echo "                              re-run this prompt"
      echo "  claude, then type /login    sign in with a claude.ai subscription"
      echo "                              (an ANTHROPIC_API_KEY in $env_file would"
      echo "                              take precedence -- remove that file first)"
      ;;
  esac
  unset _choice _leftover_litellm_key
  # Nothing to load into this shell: `claude` reads $env_file itself.
fi
unset -f _has_backend

# ---------------------------------------------------------------------------
# 6. Sync MCP servers from the registry (non-fatal)
# ---------------------------------------------------------------------------

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
  _warn "Warning: MCP servers weren't synced (Claude Code itself is installed)." \
        "  Once the registry is reachable: $script_dir/sync-mcp.sh"
fi

echo "Claude Code ${_claude_version:-(version unknown)} ready, $_mcp_summary. Run: claude"
echo "In a new shell, first: . $script_dir/env.sh"

unset script_dir claude_bin settings_file settings_example env_file claude_md_target claude_md_source
unset _refresh_settings _reset_login _verbose _version _claude_version _sync_out _sync_notes _mcp_count _mcp_summary
unset -f _detail _warn
