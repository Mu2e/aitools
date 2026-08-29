#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  install.sh <deploy-root> <ref> [repo-url]

Installs ecl-mcp into <deploy-root>/releases/<ref>/.venv using uv, pinned to
the given git ref (tag/branch/commit) of the aitools repo, via uv's git
subdirectory install syntax:

  uv venv <deploy-root>/releases/<ref>/.venv
  uv pip install --python <...>/.venv/bin/python \
    "ecl-mcp @ git+<repo-url>@<ref>#subdirectory=mcp/ecl"

This pulls in two more git dependencies transitively (see pyproject.toml):
  - ecl-api (github.com/Mu2e/ecl-api) -- the actual ECL XML/REST client
  - mikey (this same aitools repo, mcp/mikey) -- the bearer-token auth check

That's the entire install -- no source tree is copied. `uv pip install`
already produces everything needed to run it:

  <...>/.venv/bin/ecl-mcp                     (python entry point)
  <...>/.venv/bin/ecl-mcp.sh                   (bash wrapper -- server-side
                                                 setup hook, execs ecl-mcp;
                                                 currently a no-op passthrough)
  <...>/.venv/bin/ecl-mcp-install-unit.sh      (renders + links the systemd
                                                 --user unit; see below)

<deploy-root>/current is symlinked to the new release. This script does not
touch systemd itself -- run the printed ecl-mcp-install-unit.sh command when
you're ready; it renders the unit into THIS release's own share/ dir and
registers it with `systemctl --user link` (a symlink in ~/.config pointing
back here, not a copy -- see ecl-mcp-install-unit.sh --help).

Examples:
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/ecl v0.1.0
  ./scripts/install.sh /exp/mu2e/app/users/mu2epro/mcp/deploy/ecl main \
      https://github.com/Mu2e/aitools

Notes:
  - Run as the account that will run the systemd --user service.
  - Requires `uv` on PATH.
  - ecl-mcp needs no mu2e/cvmfs offline-software environment -- it's a
    plain HTTP client (stdlib + requests) reaching the ECL's HTTPS endpoint,
    so there's nothing beyond the venv itself for ecl-mcp.sh to set up.
  - Unlike dqm/registry, this server needs real credentials (ECL_URL,
    ECL_USER_NAME, ECL_PASSWORD) and normally MIKEY_KEYS_FILE to run for
    real -- see ecl-mcp-install-unit.sh --env-file, and never pass these as
    CLI flags (they'd show up in `ps` and in the rendered systemd unit).
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

echo "[2/2] Installing ecl-mcp from ${repo_url}@${ref} (subdirectory: mcp/ecl)"
uv pip install --python "$venv_dir/bin/python" \
  "ecl-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/ecl"

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo
echo "Next steps:"
echo "  1. Create an env file with ECL_URL, ECL_USER_NAME, ECL_PASSWORD, MIKEY_KEYS_FILE"
echo "     (see README.md -- Environment variables)"
echo "  2. $venv_dir/bin/ecl-mcp.sh --check   # after: set -a; source <env-file>; set +a"
echo "  3. $venv_dir/bin/ecl-mcp-install-unit.sh --port 8005 --env-file /path/to/env-file"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
