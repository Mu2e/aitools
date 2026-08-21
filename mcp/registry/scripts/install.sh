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
  <...>/.venv/share/registry-mcp/ports.json    (this release's registry
                                                 file, built from
                                                 mcp/registry/config/ports.json
                                                 at the pinned ref -- this is
                                                 the file --registry should
                                                 point at; see Notes below)

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
  - registry-file defaults to THIS release's own installed copy,
    <deploy-root>/releases/<ref>/.venv/share/registry-mcp/ports.json (built
    from mcp/registry/config/ports.json at the pinned ref) -- the same
    default registry-mcp itself falls back to via sys.prefix when no
    --registry is given. Point --registry at that path (or let
    registry-mcp-install-unit.sh's suggested command do it for you) so that
    servers only get added/removed by editing config/ports.json in git and
    cutting a new release -- NOT by hand-editing any deployed file, and
    especially not a path inside a personal checkout (a checkout's
    config/ports.json is a live-read, uncached file for whatever process
    happens to be pointed at it -- editing it takes effect immediately, with
    no review gate, if anything is misconfigured to read it directly).
  - Pass an explicit 4th argument only to deliberately override this
    convention (e.g. a deploy-root-level copy meant to persist independently
    of any one release); it is not needed for normal use.
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

release_dir="$deploy_root/releases/$ref"
current_link="$deploy_root/current"
venv_dir="$release_dir/.venv"

# Must come after venv_dir is set above -- this default references it.
# Deliberately NOT checkout-relative: defaulting to a path inside whatever
# checkout install.sh happens to be run from was the actual root cause of a
# real incident (an edit to a checkout's config/ports.json went live
# immediately because the deployed unit's --registry pointed there instead
# of at this release's own installed copy). See Notes above.
registry_file="${4:-$venv_dir/share/registry-mcp/ports.json}"

mkdir -p "$release_dir"

echo "[1/2] Creating venv: $venv_dir"
uv venv "$venv_dir"

echo "[2/2] Installing registry-mcp from ${repo_url}@${ref} (subdirectory: mcp/registry)"
uv pip install --python "$venv_dir/bin/python" \
  "registry-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/registry"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo "Registry file to use: $registry_file"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/registry-mcp.sh --check"
echo "  2. $venv_dir/bin/registry-mcp-install-unit.sh --port 8000 --registry $registry_file"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
