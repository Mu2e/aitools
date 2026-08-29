#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  install.sh <deploy-root> <ref> [repo-url]

Installs runs-mcp into <deploy-root>/releases/<ref>/.venv using uv, pinned
to the given git ref (tag/branch/commit) of the aitools repo, via uv's git
subdirectory install syntax:

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --python <...>/.venv/bin/python \
    "runs-mcp @ git+<repo-url>@<ref>#subdirectory=mcp/runs"

This pulls in one more git dependency transitively (see pyproject.toml):
mikey (this same aitools repo, mcp/mikey) -- the bearer-token auth check.

That's the entire install -- no source tree is copied. `uv pip install`
already produces everything needed to run it:

  <...>/.venv/bin/runs-mcp                     (python entry point)
  <...>/.venv/bin/runs-mcp.sh                   (bash wrapper -- sources
                                                  the Offline environment
                                                  via `muse setup`, THEN
                                                  execs runs-mcp; see
                                                  runs-mcp.sh itself)
  <...>/.venv/bin/runs-mcp-install-unit.sh      (renders + links the
                                                  systemd --user unit; see
                                                  below)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd itself -- run the printed runs-mcp-install-unit.sh command
when you're ready; it renders the unit into THIS release's own share/ dir
and registers it with `systemctl --user link` (a symlink in ~/.config
pointing back here, not a copy -- see runs-mcp-install-unit.sh --help).

Examples:
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/runs v0.1.0
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/runs main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service.
  - Requires `uv` on PATH.
  - UNLIKE dqm/registry/ecl, this server DOES need the Offline/mu2e
    environment (for the runTool binary) -- but that's handled entirely
    inside runs-mcp.sh via `muse setup`, once per server start, not
    something this install script or the venv itself needs to worry
    about. No credentials are needed (runTool uses the same ambient
    Query-Engine access as dqm-mcp -- no secrets to manage here, unlike
    ecl-mcp).
USAGE
  exit 2
}

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found on PATH" >&2
  exit 2
fi

deploy_root="$1"
ref="$2"
repo_url="${3:-https://github.com/Mu2e/aitools}"

release_dir="$deploy_root/releases/$ref"
current_link="$deploy_root/current"
venv_dir="$release_dir/.venv"

mkdir -p "$release_dir"

echo "[1/2] Creating venv: $venv_dir"
uv venv "$venv_dir"

echo "[2/2] Installing runs-mcp from ${repo_url}@${ref} (subdirectory: mcp/runs)"
uv pip install --python "$venv_dir/bin/python" \
  "runs-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/runs"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/runs-mcp.sh --check"
echo "  2. $venv_dir/bin/runs-mcp-install-unit.sh --port 8006"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
