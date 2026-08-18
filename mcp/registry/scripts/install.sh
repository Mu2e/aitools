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
    "registry-mcp @ git+<repo-url>@<ref>#subdirectory=aitools/mcp/registry"

That's the entire install -- no source tree is copied, no config is
generated. `uv pip install` already produces everything needed to run it:

  <...>/.venv/bin/registry-mcp      (python entry point)
  <...>/.venv/bin/registry-mcp.sh   (bash wrapper -- spack/module setup hook,
                                      then execs its sibling registry-mcp)

<deploy-root>/current is symlinked to the new release, and a ready-to-use
systemd unit is rendered to <deploy-root>/registry-mcp.service, with
ExecStart pointing at current/.venv/bin/registry-mcp.sh.

Examples:
  ./scripts/install.sh /exp/mu2e/app/home/mu2eai/mcp/deploy/registry v0.1.0
  ./scripts/install.sh /exp/mu2e/app/home/mu2eai/mcp/deploy/registry main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service (e.g. mu2eai).
  - Requires `uv` on PATH.
  - registry-file defaults to config/ports.json next to this script; pass an
    absolute path explicitly to keep it stable regardless of which checkout
    you happen to run this from.
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

echo "[1/3] Creating venv: $venv_dir"
uv venv "$venv_dir"

echo "[2/3] Installing registry-mcp from ${repo_url}@${ref} (subdirectory: aitools/mcp/registry)"
uv pip install --python "$venv_dir/bin/python" \
  "registry-mcp @ git+${repo_url}@${ref}#subdirectory=aitools/mcp/registry"

echo "[3/3] Updating current symlink and rendering systemd unit"
ln -sfn "$release_dir" "$current_link"

unit_file="$deploy_root/registry-mcp.service"
cat > "$unit_file" <<EOF
[Unit]
Description=registry-mcp (trivial HTTP MCP server with a live registry endpoint)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$current_link/.venv/bin/registry-mcp.sh --port=8000 --registry=$registry_file
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo "Entry point: $venv_dir/bin/registry-mcp.sh"
echo "Registry file: $registry_file"
echo "Rendered unit: $unit_file"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/registry-mcp.sh --check"
echo "  2. mkdir -p ~/.config/systemd/user"
echo "  3. cp '$unit_file' ~/.config/systemd/user/registry-mcp.service"
echo "  4. systemctl --user daemon-reload"
echo "  5. systemctl --user enable --now registry-mcp"
echo "  6. systemctl --user status registry-mcp"
