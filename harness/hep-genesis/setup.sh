#!/usr/bin/env bash
# Usage:
#   . setup.sh      (sourced -- also leaves hep-genesis on PATH in your shell)
#   ./setup.sh      (executed -- installs only, doesn't touch your shell)
#
# First use, and safe to re-run: installs hep-genesis if there is no install
# at $HEP_GENESIS_ROOT (see env.sh, which this always sources), creates your
# data folder with the Mu2e settings, and loads the environment. In later
# shells, `. env.sh` is enough.
#
# An install that exists is left alone, also when hep-genesis.pin has moved
# on since: to upgrade, run install.sh yourself (it keeps the previous
# release to roll back to).
#
# Deliberately no `set -e`/`set -u` at this file's top level: it is meant to
# be sourced, and those would leak into your interactive shell. install.sh
# keeps its own, as it is always run as a subprocess.

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  cat >&2 <<'USAGE'
Usage:
  . setup.sh
  ./setup.sh
USAGE
  return 2 2>/dev/null || exit 2
fi

hg_setup_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
if [[ -z "$hg_setup_dir" ]]; then
  echo "Error: could not resolve script directory" >&2
  return 1 2>/dev/null || exit 1
fi

# Not an error yet if this reports "not installed": that is what we fix next.
source "$hg_setup_dir/env.sh" 2>/dev/null

if [[ -x "$HEP_GENESIS_ROOT/current/bin/hep-genesis-agent" ]]; then
  hg_have="$(sed -n 's/^hep-genesis //p' "$HEP_GENESIS_ROOT/current/VERSION" 2>/dev/null)"
  hg_want="$(sed -n 's/^ref=//p' "$hg_setup_dir/hep-genesis.pin")"
  echo "hep-genesis already installed: $HEP_GENESIS_ROOT/current (${hg_have:0:10})"
  if [[ -n "$hg_want" && "$hg_have" != "$hg_want" ]]; then
    echo "  The harness now pins ${hg_want:0:10}. To upgrade: bash $hg_setup_dir/install.sh $HEP_GENESIS_ROOT"
  fi
  unset hg_have hg_want
else
  echo "No hep-genesis at $HEP_GENESIS_ROOT -- installing (a minute or two)"
  if ! bash "$hg_setup_dir/install.sh" "$HEP_GENESIS_ROOT"; then
    echo "Error: the install failed" >&2
    unset hg_setup_dir
    return 1 2>/dev/null || exit 1
  fi
fi

if ! source "$hg_setup_dir/env.sh"; then
  echo "Error: failed to source env.sh" >&2
  unset hg_setup_dir
  return 1 2>/dev/null || exit 1
fi

# Your data folder, with the Mu2e settings on first use. Private: the .env
# holds keys.
if [[ ! -d "$HG_DATA_DIR" ]]; then
  if ! ( umask 077 && mkdir -p "$HG_DATA_DIR" ); then
    echo "Error: cannot create $HG_DATA_DIR (set HG_DATA_DIR)" >&2
    unset hg_setup_dir
    return 1 2>/dev/null || exit 1
  fi
fi
( umask 077 && mkdir -p "$HEP_PROJECTS_DIR" "$HEP_WORKSPACE_DIR" "$HEP_STAGING_DIR" "$DISPATCH_ARTIFACT_DIR" )
if [[ ! -e "$HEP_ENV_FILE" ]]; then
  ( umask 077 && cp "$hg_setup_dir/site/env.template" "$HEP_ENV_FILE" ) \
    && echo "Created $HEP_ENV_FILE (default model: vllm.fnal.gov, no key needed)"
fi
if [[ ! -e "$HEP_SERVERS_FILE" ]]; then
  ( umask 077 && cp "$hg_setup_dir/site/servers.json" "$HEP_SERVERS_FILE" ) \
    && echo "Created $HEP_SERVERS_FILE (the Mu2e MCP servers)"
fi

echo
echo "hep-genesis is ready. Data: $HG_DATA_DIR"
echo "  hep-genesis-web      web UI (prints an ssh tunnel line and a URL)"
echo "  hep-genesis-agent    command line (--help)"
echo "In a new shell:  . $hg_setup_dir/env.sh"
unset hg_setup_dir
