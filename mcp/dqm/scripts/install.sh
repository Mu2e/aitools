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
    a plain HTTP client (requests + pandas, via dqm.reader) reaching the
    Query Engine's public HTTPS endpoint, so there's nothing beyond the venv
    itself for dqm-mcp.sh to set up.
  - Every database query lives in `dqm.reader`, from the mu2e-dqm
    distribution, which ships in a DIFFERENT repository (Mu2e/DQM). It is a
    pinned git dependency in pyproject.toml,
    so the single `uv pip install` above pulls it in automatically and no
    extra step is needed.

    To deploy against a different mu2e-dqm revision than the pinned one --
    testing a reader change before tagging it, say -- pass --dqm-ref. That
    installs the named revision over the pinned one after the main install:

      ./scripts/install.sh /path/to/deploy/dqm v0.3.0 --dqm-ref my-branch

    Prefer bumping the pin in pyproject.toml for anything permanent, so the
    release stays reproducible from its ref alone.
USAGE
  exit 2
}

deploy_root=""
ref=""
repo_url="https://github.com/Mu2e/aitools"
dqm_repo_url="https://github.com/Mu2e/DQM"
dqm_ref=""
positional=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dqm-ref)
      [[ $# -ge 2 ]] || { echo "ERROR: --dqm-ref needs a value" >&2; exit 2; }
      dqm_ref="$2"; shift 2 ;;
    --dqm-repo-url)
      [[ $# -ge 2 ]] || { echo "ERROR: --dqm-repo-url needs a value" >&2; exit 2; }
      dqm_repo_url="$2"; shift 2 ;;
    -h|--help)
      usage ;;
    -*)
      echo "ERROR: unknown option $1" >&2; usage ;;
    *)
      positional+=("$1"); shift ;;
  esac
done

set -- "${positional[@]+"${positional[@]}"}"

if [[ $# -lt 2 || $# -gt 3 ]]; then
  usage
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found on PATH" >&2
  exit 2
fi

deploy_root="$1"
ref="$2"
repo_url="${3:-$repo_url}"

release_dir="$deploy_root/releases/$ref"
current_link="$deploy_root/current"
venv_dir="$release_dir/.venv"

steps=2
[[ -n "$dqm_ref" ]] && steps=3

mkdir -p "$release_dir"

echo "[1/$steps] Creating venv: $venv_dir"
uv venv "$venv_dir"

echo "[2/$steps] Installing dqm-mcp from ${repo_url}@${ref} (subdirectory: mcp/dqm)"
echo "         (pulls in mu2e-dqm from Mu2e/DQM at the ref pinned in pyproject.toml)"
uv pip install --python "$venv_dir/bin/python" \
  "dqm-mcp @ git+${repo_url}@${ref}#subdirectory=mcp/dqm"

if [[ -n "$dqm_ref" ]]; then
  # Installed after dqm-mcp so it replaces the pinned reader rather than being
  # replaced by it -- pip resolves a direct URL requirement to exactly that
  # URL, so the order matters.
  echo "[3/$steps] Overriding mu2e-dqm with ${dqm_repo_url}@${dqm_ref}"
  uv pip install --python "$venv_dir/bin/python" --reinstall-package mu2e-dqm \
    "mu2e-dqm @ git+${dqm_repo_url}@${dqm_ref}#subdirectory=python"
fi

ln -sfn "$release_dir" "$current_link"

echo "Done."
echo "Current release: $current_link -> $release_dir"
echo
echo "Next steps:"
echo "  1. $venv_dir/bin/dqm-mcp.sh --check"
echo "  2. $venv_dir/bin/dqm-mcp-install-unit.sh --port 8001"
echo "     (renders + links the systemd unit and enables/starts it -- pass --no-enable to skip that last part)"
