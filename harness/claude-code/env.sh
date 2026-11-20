# Source this file to use claude from this harness:
#   . env.sh
#
# Kept separate from setup.sh (which installs the binary) so that using
# claude is an explicit, per-shell opt-in rather than a permanent PATH
# change in ~/.bashrc.
#
# Also sourced internally by setup.sh.

env_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# CLAUDE_HARNESS_ROOT overrides the default location -- mainly for testing
# a fresh install without touching your real config, e.g.:
#   CLAUDE_HARNESS_ROOT=/tmp/claude-test . setup.sh
claude_root="${CLAUDE_HARNESS_ROOT:-/exp/mu2e/app/users/$USER/claude-code}"
export CLAUDE_ROOT="$claude_root"

export CLAUDE_BIN_DIR="$claude_root/bin"
export CLAUDE_HOME="$claude_root/home"

# Add a local bin dir to PATH -- holds the thin `claude` wrapper script
# written by setup.sh, through which the native binary is invoked.
case ":$PATH:" in
  *":$CLAUDE_BIN_DIR:"*) ;;
  *) export PATH="$CLAUDE_BIN_DIR:$PATH" ;;
esac

# Claude Code has no XDG relocation of its own: it writes ~/.claude.json
# and ~/.claude/ using $HOME, and uses $XDG_CACHE_HOME for MCP logs and
# other cache data. Home dirs have tight disk quotas, so redirect both to
# group storage exactly as the goose harness does.
mkdir -p "$CLAUDE_HOME" "$claude_root/cache"
export XDG_CACHE_HOME="$claude_root/cache"

# Shared context file (mu2e.md) -- used by the goose harness via TOM and
# by Claude Code via CLAUDE.md symlink (managed by setup.sh). Exported so
# other scripts can reference the canonical path.
export MU2E_CONTEXT_FILE="$env_script_dir/../shared/mu2e.md"

# LiteLLM endpoint (same server the goose harness uses, different API path).
# Claude Code speaks the *Anthropic* API natively, and litellm.fnal.gov
# exposes an Anthropic-compatible proxy at /anthropic -- so we point
# ANTHROPIC_BASE_URL there and Claude Code needs no other changes.
# Users with a personal Anthropic API key should leave ANTHROPIC_BASE_URL
# unset; setup.sh sets it when the litellm backend is chosen.
#
# These backend settings (key, base URL, model mapping) live in a per-user
# env file, $CLAUDE_HOME/.env. It is deliberately NOT sourced here: that
# would export your API key to everything you run from this shell. It is
# loaded only for the claude invocation itself (below, and in the wrapper
# script setup.sh writes) and by claudebox.sh.

# Wrap claude so:
#   1. HOME points at quota-free group storage (keeps ~/.claude.json and
#      ~/.claude/ out of /nashome).
#   2. KRB5CCNAME is redirected to a non-existent path so Claude Code (and
#      anything it spawns) cannot use your Kerberos ticket.  This does NOT
#      call kdestroy -- the real ticket stays valid for your own shell.
#   3. umask 077 ensures any config/key files created are private.
#   4. The backend settings in $CLAUDE_HOME/.env are loaded for this
#      invocation only.
claude() {
  (
    # shellcheck source=/dev/null
    [[ -f "$CLAUDE_HOME/.env" ]] && source "$CLAUDE_HOME/.env"
    export HOME="$CLAUDE_HOME"
    export KRB5CCNAME="/tmp/.claude-no-krb5-cache-$$"
    umask 077
    command claude "$@"
  )
}

# Convenience alias (same pattern as mu2eai for goose).
mu2eai-claude() {
  claude "$@"
}

# Sandboxed Claude: claudebox (Claude Code in an apptainer container,
# ../claudebox/claudebox) with the harness defaults -- native binary,
# litellm model mapping, Mu2e MCP servers and key, Mu2e context. See
# claudebox.sh. This shadows a plain `claudebox` on PATH (the CVMFS bin
# one); `command claudebox` runs that without the harness defaults.
claudebox() {
  "$CLAUDE_HARNESS_DIR/claudebox.sh" "$@"
}

# Export the harness directory so the shell functions above can find their
# scripts even after env_script_dir is unset (functions are evaluated lazily).
export CLAUDE_HARNESS_DIR="$env_script_dir"
unset env_script_dir
