# Source this file to use hep-genesis in this shell:
#   . env.sh
#   hep-genesis-web                  # the web UI (prints the ssh tunnel line and the URL)
#   hep-genesis-agent --help         # the command line
#
# Kept separate from setup.sh/install.sh (which build the install), as in the
# other harnesses: using hep-genesis is a per-shell opt-in, not a permanent
# PATH change. Also sourced by setup.sh, so the locations are defined once.
#
# Two locations (override either before sourcing):
#   HEP_GENESIS_ROOT  the install: releases/ and current
#                     (default /exp/mu2e/app/users/$USER/hep-genesis; point it
#                     at a shared install to use that one)
#   HG_DATA_DIR       your data, mode 700: .env (settings, keys), servers.json,
#                     models.json, projects/, workspace/
#                     (default /exp/mu2e/data/users/$USER/hep-genesis-data)
# Nothing is written next to this file: it works from a read-only copy, and
# from inside an install (<install>/current/env.sh), which then uses itself.
#
# Sourced, so no `set -e` and no `exit`: a failure must not close your shell.

hg_env_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# The install this shell uses: the release this file is in, when it is in
# one; else <root>/current.
export HEP_GENESIS_ROOT="${HEP_GENESIS_ROOT:-/exp/mu2e/app/users/$USER/hep-genesis}"
if [ -x "$hg_env_dir/bin/hep-genesis-agent" ]; then
    hg_release="$hg_env_dir"
else
    hg_release="$HEP_GENESIS_ROOT/current"
fi
export HG_DATA_DIR="${HG_DATA_DIR:-/exp/mu2e/data/users/$USER/hep-genesis-data}"

# The code reads these before it loads the .env, so they are exported here,
# not set in the .env. Without them it would use ~/hep-genesis.
export HEP_PROJECTS_DIR="$HG_DATA_DIR/projects"
export HEP_WORKSPACE_DIR="$HG_DATA_DIR/workspace"
export HEP_STAGING_DIR="$HG_DATA_DIR/staging"
export DISPATCH_ARTIFACT_DIR="$HG_DATA_DIR/artifacts"
export HEP_ENV_FILE="$HG_DATA_DIR/.env"
export HEP_SERVERS_FILE="$HG_DATA_DIR/servers.json"
# Mu2e's context for the model and its model presets, from the site files
# next to this file (a `git pull`, or a new release, updates them). These
# two are set here whatever the shell had: a stale value from an older setup
# would otherwise point into a checkout that may be gone. For your own
# files, set them in the .env, which wins over the shell.
export HEP_CONTEXT_FILE="$hg_env_dir/site/context.md"
export HEP_MODEL_PRESETS_FILE="$hg_env_dir/site/models.json"

# Usage accounting, as in the other harnesses: after every turn hep-genesis
# runs this logger, which writes the chat's counts (model calls, tokens, tool
# names; no user, no content) to $HG_DATA_DIR/log/usage.jsonl, or where
# LOG_OUTPUT says. Set HEP_TURN_HOOK= in the .env to switch it off.
if [ -f "$hg_release/usage/log-session.py" ]; then
    export HEP_TURN_HOOK="$hg_release/venv/bin/python $hg_release/usage/log-session.py"
fi

hg_status=0
if [ -x "$hg_release/bin/hep-genesis-agent" ]; then
    case ":$PATH:" in
        *":$hg_release/bin:"*) ;;
        *) export PATH="$hg_release/bin:$PATH" ;;   # only the hep-genesis commands, not the install's python
    esac
else
    echo "hep-genesis is not installed at $HEP_GENESIS_ROOT: run  . $hg_env_dir/setup.sh" >&2
    hg_status=1
fi
unset hg_env_dir hg_release
return $hg_status 2>/dev/null || true
