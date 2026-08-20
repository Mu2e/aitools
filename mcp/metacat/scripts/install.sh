#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  install.sh <deploy-root> <ref> [repo-url]

Installs metacat-mcp into <deploy-root>/releases/<ref>/.venv using uv,
pinned to the given git ref (tag/branch/commit) of the aitools repo, via
uv's git subdirectory install syntax:

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --python <...>/.venv/bin/python \
    "metacat-mcp @ git+<repo-url>@<ref>#subdirectory=mcp/metacat"

That's the entire install -- no source tree is copied. `uv pip install`
already produces everything needed to run it:

  <...>/.venv/bin/metacat-mcp                     (python entry point)
  <...>/.venv/bin/metacat-mcp.sh                   (bash wrapper -- server-side
                                                     setup hook, execs metacat-mcp;
                                                     currently a no-op passthrough)
  <...>/.venv/bin/metacat-mcp-install-unit.sh      (renders + links the systemd
                                                     --user unit; see below)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd itself -- run the printed metacat-mcp-install-unit.sh command
when you're ready; it renders the unit into THIS release's own share/ dir
and registers it with `systemctl --user link` (a symlink in ~/.config
pointing back here, not a copy -- see metacat-mcp-install-unit.sh --help).

Examples:
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/metacat v0.2.0
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/metacat main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service.
  - Requires `uv` on PATH.
  - metacat-mcp needs no mu2e/cvmfs offline-software environment -- its only
    metacat-specific dependency is the `metacat-client` PyPI package (a
    pinned dependency of this project, installed automatically by uv into
    the venv above), configured via the METACAT_SERVER_URL /
    METACAT_AUTH_SERVER_URL environment variables. There's nothing beyond
    the venv itself for metacat-mcp.sh to set up.
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

echo "[2/2] Installing metacat-mcp from ${repo_url}@${ref} (subdirectory: mcp/metacat)"
uv pip install --python "$venv_dir/bin/python" \
  "metacat-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/metacat"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/metacat-mcp.sh --check"
echo "  2. $venv_dir/bin/metacat-mcp-install-unit.sh --port 8002"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
