#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  install.sh <deploy-root> <ref> [repo-url] [registry-file]

Installs registry-mcp into <deploy-root>/releases/<ref>/.venv using uv,
pinned to the given git ref (tag/branch/commit) of the aitools repo, via
uv's git subdirectory install syntax:

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --python <...>/.venv/bin/python \
    "registry-mcp @ git+<repo-url>@<ref>#subdirectory=mcp/registry"

That's the entire install -- no source tree is copied. `uv pip install`
already produces everything needed to run it:

  <...>/.venv/bin/registry-mcp                (python entry point)
  <...>/.venv/bin/registry-mcp.sh              (bash wrapper -- spack/module
                                                 setup hook, execs registry-mcp)
  <...>/.venv/bin/registry-mcp-install-unit.sh (renders + links the systemd
                                                 --user unit; see below)
  <...>/.venv/share/registry-mcp/ports.json    (shipped template -- copy it
                                                 out and edit; not something
                                                 upgrades are expected to
                                                 preserve edits to)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd itself -- run the printed registry-mcp-install-unit.sh command
when you're ready; it renders the unit into THIS release's own share/ dir
and registers it with `systemctl --user link` (a symlink in ~/.config
pointing back here, not a copy -- see registry-mcp-install-unit.sh --help).

Examples:
  ./scripts/install.sh /exp/mu2e/app/home/mu2eai/mcp/deploy/registry v0.1.0
  ./scripts/install.sh /exp/mu2e/app/home/mu2eai/mcp/deploy/registry main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service (e.g. mu2eai).
  - Requires `uv` on PATH.
  - registry-file defaults to config/ports.json next to this script; pass an
    absolute path explicitly to keep it stable regardless of which checkout
    you happen to run this from. This is only what gets printed in the
    suggested next-step command -- copy it to your own path and edit it
    first, same as the shipped share/ template.
USAGE
  exit 2
}

if [[ $# -lt 2 || $# -gt 4 ]]; then
  usage
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found on PATH" >&2
  exit 2
fi

deploy_root="$1"
ref="$2"
repo_url="${3:-https://github.com/Mu2e/aitools}"

script_dir="$(cd "$(dirname "$0")" && pwd)"
checkout_dir="$(cd "$script_dir/.." && pwd)"
registry_file="${4:-$checkout_dir/config/ports.json}"

release_dir="$deploy_root/releases/$ref"
current_link="$deploy_root/current"
venv_dir="$release_dir/.venv"

mkdir -p "$release_dir"

echo "[1/2] Creating venv: $venv_dir"
uv venv "$venv_dir"

echo "[2/2] Installing registry-mcp from ${repo_url}@${ref} (subdirectory: mcp/registry)"
uv pip install --python "$venv_dir/bin/python" \
  "registry-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/registry"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo "Registry file to use: $registry_file (copy it out and edit before using)"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/registry-mcp.sh --check"
echo "  2. $venv_dir/bin/registry-mcp-install-unit.sh --port 8000 --registry $registry_file"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
