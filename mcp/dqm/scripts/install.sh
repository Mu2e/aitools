#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  install.sh <deploy-root> <ref> [repo-url]

Installs dqm-mcp into <deploy-root>/releases/<ref>/.venv using uv, pinned to
the given git ref (tag/branch/commit) of the aitools repo, via uv's git
subdirectory install syntax:

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --python <...>/.venv/bin/python \
    "dqm-mcp @ git+<repo-url>@<ref>#subdirectory=mcp/dqm"

That's the entire install -- no source tree is copied. `uv pip install`
already produces everything needed to run it:

  <...>/.venv/bin/dqm-mcp                     (python entry point)
  <...>/.venv/bin/dqm-mcp.sh                   (bash wrapper -- server-side
                                                 setup hook, execs dqm-mcp;
                                                 currently a no-op passthrough)
  <...>/.venv/bin/dqm-mcp-install-unit.sh      (renders + links the systemd
                                                 --user unit; see below)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd itself -- run the printed dqm-mcp-install-unit.sh command when
you're ready; it renders the unit into THIS release's own share/ dir and
registers it with `systemctl --user link` (a symlink in ~/.config pointing
back here, not a copy -- see dqm-mcp-install-unit.sh --help).

Examples:
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/dqm v0.2.0
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/dqm main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service.
  - Requires `uv` on PATH.
  - dqm-mcp needs no mu2e/cvmfs offline-software environment at all -- it's
    a plain HTTP client (stdlib + requests) reaching the Query Engine's
    public HTTPS endpoint, so there's nothing beyond the venv itself for
    dqm-mcp.sh to set up.
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

echo "[2/2] Installing dqm-mcp from ${repo_url}@${ref} (subdirectory: mcp/dqm)"
uv pip install --python "$venv_dir/bin/python" \
  "dqm-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/dqm"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/dqm-mcp.sh --check"
echo "  2. $venv_dir/bin/dqm-mcp-install-unit.sh --port 8001"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
