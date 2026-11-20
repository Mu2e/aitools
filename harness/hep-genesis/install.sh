#!/usr/bin/env bash
# Install hep-genesis (Mu2e/hep-genesis-agent, at the commit pinned in
# hep-genesis.pin) under DEST, for users or for the mu2eai service:
#
#   harness/hep-genesis/install.sh DEST [--ref REF] [--no-ui] [--keep N]
#
#   DEST/releases/<commit>/   src/      the checkout (no .git), with the built web UI
#                             venv/     Python environment (uv)
#                             bin/      hep-genesis-agent, -web, -service, hep-genesis-watch,
#                                       hep-genesis-add-automation
#                             site/ automations/ systemd/ env.sh     from this folder
#                             usage/    the usage logger (log-session.py)
#                             VERSION   hep-genesis commit + aitools commit
#   DEST/current              -> the release just installed
#   DEST/python/              the Python uv downloaded (shared by the releases)
#   DEST/bin/uv               uv itself (copied from PATH, or downloaded)
#
# Users of a shared install then `source DEST/current/env.sh`; for your own,
# setup.sh calls this for you. The service runs DEST/current/bin
# through the units of systemd/install-unit.sh. Rolling back is repointing
# DEST/current at an older release (and restarting the units).
#
# Needs: git with read access to the repository (it is private), and for
# the web UI node >= 20 with npm (on PATH, or an nvm under $NVM_DIR). --no-ui
# skips the UI: enough for a service that only runs automations.
#
#   --ref REF    install this commit/branch/tag instead of the pinned one
#   --keep N     releases to keep (default 2: this one and one to roll back to)
#
# Environment: HEP_GENESIS_REPO overrides the pin's repository URL. The pin
# uses ssh (git@github.com:...); when that fails, the same repository is
# tried over https, which works with `gh auth setup-git`.
# UV_CACHE_DIR defaults to /tmp/$USER/uv-cache.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" >&2; exit 2; }

dest="" ref="" ui=1 keep=2
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --ref) ref="${2:?--ref needs a value}"; shift 2 ;;
    --keep) keep="${2:?--keep needs a number}"; shift 2 ;;
    --no-ui) ui=0; shift ;;
    -*) echo "unknown option $1" >&2; usage ;;
    *) [[ -z "$dest" ]] || usage; dest="$1"; shift ;;
  esac
done
[[ -n "$dest" ]] || usage

pin() { sed -n "s/^$1=//p" "$here/hep-genesis.pin"; }
repo="${HEP_GENESIS_REPO:-$(pin repo)}"
ref="${ref:-$(pin ref)}"
[[ -n "$repo" && -n "$ref" ]] || { echo "ERROR: no repo/ref (see $here/hep-genesis.pin)" >&2; exit 2; }

command -v git >/dev/null 2>&1 || { echo "ERROR: git not found on PATH" >&2; exit 2; }

umask 022                                       # the install is read by other accounts
mkdir -p "$dest"
dest="$(cd "$dest" && pwd -P)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/$USER/uv-cache}"
export UV_LINK_MODE=copy                        # cache and venv are on different filesystems
export UV_PYTHON_INSTALL_DIR="$dest/python"     # not ~/.local: other users (and the service) must reach it

# uv, kept in DEST/bin rather than ~/.local/bin (the NAS home): the one on
# PATH if there is one, else Astral's installer (as kb-mcp's setup does).
mkdir -p "$dest/bin"
if [[ ! -x "$dest/bin/uv" ]]; then
  if have="$(command -v uv 2>/dev/null)" && cp "$have" "$dest/bin/uv" 2>/dev/null; then
    chmod 755 "$dest/bin/uv"
  else
    echo "Installing uv into $dest/bin"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$dest/bin" INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
  fi
fi
[[ -x "$dest/bin/uv" ]] || { echo "ERROR: no uv in $dest/bin (install it there, or put uv on PATH)" >&2; exit 2; }
export PATH="$dest/bin:$PATH"

tmp="$(mktemp -d "$dest/.install.XXXXXX")"
release="" done_ok=0
cleanup() {
  rm -rf "$tmp"
  # A release that did not finish is removed, so the next try starts clean.
  if [[ "$done_ok" != 1 && -n "$release" && -d "$release" ]]; then
    echo "Removing the unfinished release $release" >&2
    rm -rf "$release"
  fi
}
trap cleanup EXIT

echo "[1/5] Fetching $ref from $repo"
git init -q "$tmp/src"
git -C "$tmp/src" remote add origin "$repo"
fetched=0
if git -C "$tmp/src" fetch -q --depth 1 origin "$ref"; then
  fetched=1
elif [[ "$repo" == git@github.com:* ]]; then
  # No ssh key on GitHub from this machine: the same repository over https,
  # which works with the gh CLI logged in (`gh auth setup-git`). Never asks
  # for a password: that would hang a sourced setup.sh.
  https="https://github.com/${repo#git@github.com:}"
  echo "      ssh did not work; trying $https"
  git -C "$tmp/src" remote set-url origin "$https"
  GIT_TERMINAL_PROMPT=0 git -C "$tmp/src" fetch -q --depth 1 origin "$ref" && fetched=1
fi
if [[ "$fetched" != 1 ]]; then
  echo "ERROR: could not fetch $ref from $repo" >&2
  echo "       The repository is private: git must show GitHub who you are (a member of the Mu2e" >&2
  echo "       organization). Either an ssh key registered on GitHub (test: ssh -T git@github.com)," >&2
  echo "       or the gh CLI:  gh auth login && gh auth setup-git" >&2
  exit 1
fi
git -C "$tmp/src" checkout -q --detach FETCH_HEAD
commit="$(git -C "$tmp/src" rev-parse HEAD)"
short="${commit:0:10}"
rm -rf "$tmp/src/.git"

if [[ -e "$dest/releases/$short" ]]; then
  echo "ERROR: $dest/releases/$short exists already. To reinstall it, remove it first" >&2
  echo "       (not while $dest/current points at it and something is running)." >&2
  exit 1
fi
release="$dest/releases/$short"
mkdir -p "$dest/releases"
mv "$tmp/src" "$tmp/release-src"
mkdir "$release"
mv "$tmp/release-src" "$release/src"

if [[ "$ui" == 1 ]]; then
  echo "[2/5] Building the web UI"
  node_major() { node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0; }
  if [[ "$(node_major)" -lt 20 ]]; then          # none, or the system's old one: try nvm
    export NVM_DIR="${NVM_DIR:-/exp/mu2e/app/users/$USER/.nvm}"
    if [[ -s "$NVM_DIR/nvm.sh" ]]; then
      set +u; . "$NVM_DIR/nvm.sh" >/dev/null; nvm use 22 >/dev/null 2>&1 || nvm use node >/dev/null 2>&1 || true; set -u
    fi
  fi
  if [[ "$(node_major)" -lt 20 ]] || ! command -v npm >/dev/null 2>&1; then
    echo "ERROR: the web UI needs node >= 20 with npm (found: $(node --version 2>/dev/null || echo none))." >&2
    echo "       Install one (nvm), set NVM_DIR, or pass --no-ui." >&2
    exit 2
  fi
  echo "      node $(node --version)"
  ( cd "$release/src/desktop"
    ELECTRON_SKIP_BINARY_DOWNLOAD=1 npm ci --no-audit --no-fund >"$tmp/npm.log" 2>&1 \
      && npm run build:web >>"$tmp/npm.log" 2>&1 ) || { tail -40 "$tmp/npm.log" >&2; echo "ERROR: the web UI build failed" >&2; exit 1; }
  rm -rf "$release/src/desktop/node_modules"    # ~1 GB, only needed to build
  [[ -f "$release/src/desktop/out/web/index.html" ]] || { echo "ERROR: no built UI in desktop/out/web" >&2; exit 1; }
else
  echo "[2/5] Skipping the web UI (--no-ui)"
fi

echo "[3/5] Python environment: $release/venv"
uv venv -q "$release/venv" --python 3.12
# Editable, from the release's own checkout: the package finds the built UI
# and its bundled files by their place in the source tree. The checkout is
# never edited after this.
uv pip install -q --python "$release/venv/bin/python" -e "$release/src/backend[all]" \
  --constraint "$here/site/constraints.txt"

# For the usage logger's Postgres output (LOG_PG_DSN); without it the logger
# still writes its jsonl file.
uv pip install -q --python "$release/venv/bin/python" "psycopg[binary]" \
  || echo "      NOTE: psycopg not installed: usage records go to the jsonl file only"

echo "[4/5] Site files and commands"
cp -r "$here/site" "$here/automations" "$here/systemd" "$release/"
# The usage logger (hep-genesis runs it after every turn: HEP_TURN_HOOK) with
# the record format it shares with the other harnesses.
mkdir "$release/usage"
cp "$here/log-session.py" "$here/../usage/usage_record.py" "$release/usage/"
cp "$here/env.sh" "$release/env.sh"
mkdir "$release/bin"
for cmd in hep-genesis-agent hep-genesis-web hep-genesis-service; do
  ln -s "../venv/bin/$cmd" "$release/bin/$cmd"
done
cp "$here/bin/hep-genesis-watch" "$here/bin/hep-genesis-add-automation" "$release/bin/"
chmod 755 "$release/bin/hep-genesis-watch" "$release/bin/hep-genesis-add-automation"
# Readable by other accounts whatever the modes in the aitools checkout were.
chmod -R u+rwX,go+rX,go-w "$release/site" "$release/automations" "$release/systemd" "$release/env.sh" "$release/bin" "$release/usage"
aitools="$(git -C "$here" rev-parse --short=10 HEAD 2>/dev/null || echo unknown)"
[[ -z "$(git -C "$here" status --porcelain -- "$here" 2>/dev/null)" ]] || aitools="$aitools+local"
printf 'hep-genesis %s\naitools %s\ninstalled %s\n' "$commit" "$aitools" "$(date -u +%Y-%m-%dT%H:%MZ)" > "$release/VERSION"
"$release/bin/hep-genesis-agent" --help >/dev/null   # the install imports and runs

echo "[5/5] $dest/current -> releases/$short"
ln -sfn "releases/$short" "$dest/current"
done_ok=1
# Old releases: keep the newest $keep (never the one current points at).
ls -1dt "$dest"/releases/*/ | tail -n "+$((keep + 1))" | while read -r old; do
  old="${old%/}"
  [[ "$old" == "$release" ]] && continue
  echo "      removing old release ${old##*/}"
  rm -rf "$old"
done

echo
echo "Done: $(du -sh "$release" 2>/dev/null | cut -f1) in $release"
echo "Use it:   source $dest/current/env.sh"
echo "Service:  $dest/current/systemd/install-unit.sh --help"
