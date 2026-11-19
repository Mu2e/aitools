# Source this to use hep-genesis on a Mu2e gpvm, from an installed copy:
#
#   source <install>/current/setup.sh
#   hep-genesis-web                  # the web UI (prints the ssh tunnel line and the URL)
#   hep-genesis-agent --help         # the command line
#
# Nothing is written next to this file. Your data goes to
#   $HG_DATA_DIR  (default /exp/mu2e/data/users/$USER/hep-genesis-data, mode 700):
#   .env (settings, tokens), servers.json, models.json, projects/, workspace/
# both created on first use from the Mu2e templates in site/.
#
# Sourced, so no `set -e` and no `exit`: a failure must not close your shell.

_hg_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

_hg_setup() {
    local root="$1" data="${HG_DATA_DIR:-/exp/mu2e/data/users/$USER/hep-genesis-data}"
    if [ ! -x "$root/bin/hep-genesis-agent" ]; then
        echo "ERROR: no hep-genesis install at $root (source <install>/current/setup.sh)" >&2
        return 1
    fi
    if [ ! -d "$data" ]; then
        mkdir -p "$data" && chmod 700 "$data" || { echo "ERROR: cannot create $data (set HG_DATA_DIR)" >&2; return 1; }
    fi
    # The code reads these before it loads the .env, so they are exported
    # here, not set in the .env. Without them it would use ~/hep-genesis.
    export HEP_PROJECTS_DIR="$data/projects"
    export HEP_WORKSPACE_DIR="$data/workspace"
    export HEP_STAGING_DIR="$data/staging"
    export DISPATCH_ARTIFACT_DIR="$data/artifacts"
    export HEP_ENV_FILE="$data/.env"
    export HEP_SERVERS_FILE="$data/servers.json"
    # Mu2e's context for the model and its model presets: read from the
    # install, so a new release updates them. Set your own file to override.
    export HEP_CONTEXT_FILE="${HEP_CONTEXT_FILE:-$root/site/context.md}"
    export HEP_MODEL_PRESETS_FILE="${HEP_MODEL_PRESETS_FILE:-$root/site/models.json}"
    mkdir -p "$HEP_PROJECTS_DIR" "$HEP_WORKSPACE_DIR" "$HEP_STAGING_DIR" "$DISPATCH_ARTIFACT_DIR" || return 1
    if [ ! -f "$HEP_ENV_FILE" ]; then
        cp "$root/site/env.template" "$HEP_ENV_FILE" && chmod 600 "$HEP_ENV_FILE" || return 1
        echo "Created $HEP_ENV_FILE (default model: vllm.fnal.gov, no key needed)."
    fi
    if [ ! -f "$HEP_SERVERS_FILE" ]; then
        cp "$root/site/servers.json" "$HEP_SERVERS_FILE" && chmod 600 "$HEP_SERVERS_FILE" || return 1
        echo "Created $HEP_SERVERS_FILE (the Mu2e MCP servers)."
    fi
    # Only the hep-genesis commands, not the environment's python.
    case ":$PATH:" in
        *":$root/bin:"*) ;;
        *) export PATH="$root/bin:$PATH" ;;
    esac
    export HG_ROOT="$root"
    echo "hep-genesis $(sed -n 's/^hep-genesis //p' "$root/VERSION" 2>/dev/null | cut -c1-10): data in $data"
    echo "  hep-genesis-web      web UI"
    echo "  hep-genesis-agent    command line (--help)"
}

_hg_setup "$_hg_root"
_hg_status=$?
unset -f _hg_setup
unset _hg_root
return $_hg_status 2>/dev/null || true
