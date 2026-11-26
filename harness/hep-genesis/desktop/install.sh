#!/usr/bin/env bash
# Install the hep-genesis desktop app on a laptop, set up for Mu2e:
#
#   harness/hep-genesis/desktop/install.sh [--mode local|ssh] [--ssh-host HOST]
#                                          [--dest DIR] [--ref REF]
#
# It clones Mu2e/hep-genesis-agent at the commit pinned in ../hep-genesis.pin
# (the one the gpvm install uses) into DIR (default ~/hep-genesis/desktop),
# installs its packages, and registers connect.py as the app's start hook in
# ~/hep-genesis/start-hook.json. At every start the app then gets the Mu2e
# MCP servers with your token, the site's model endpoint and the site's
# context (see connect.py). Run it again to update, or to change the mode.
#
#   --mode local     you are on the Fermilab network: on site, or over the
#                    VPN (default)
#   --mode ssh       from anywhere: connect.py forwards the servers' ports
#                    over ssh (needs a Kerberos ticket at each start)
#   --ssh-host HOST  the Mu2e machine ssh goes to (default mu2egpvm01.fnal.gov)
#   --ref REF        another commit or branch than the pinned one
#
# Needs git with read access to the repository (it is private), node >= 20
# with npm, python3, and ssh set up for Fermilab (Kerberos). macOS or Linux.
# Keep this aitools clone: the hook runs connect.py from it.
#
# Start the app with:  cd DIR/desktop && npm run dev
#
# Environment: HEP_GENESIS_REPO overrides the pin's repository URL. The pin
# uses ssh (git@github.com:...); when that fails, https is tried.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" >&2; exit 2; }

mode=local ssh_host="" dest="$HOME/hep-genesis/desktop" ref=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage ;;
    --mode) mode="${2:?--mode needs local or ssh}"; shift 2 ;;
    --ssh-host) ssh_host="${2:?--ssh-host needs a host}"; shift 2 ;;
    --dest) dest="${2:?--dest needs a folder}"; shift 2 ;;
    --ref) ref="${2:?--ref needs a value}"; shift 2 ;;
    *) echo "unknown option $1" >&2; usage ;;
  esac
done
[[ "$mode" == local || "$mode" == ssh ]] || { echo "ERROR: --mode is local or ssh" >&2; exit 2; }

pin() { sed -n "s/^$1=//p" "$here/../hep-genesis.pin"; }
repo="${HEP_GENESIS_REPO:-$(pin repo)}"
ref="${ref:-$(pin ref)}"
[[ -n "$repo" && -n "$ref" ]] || { echo "ERROR: no repo/ref (see $here/../hep-genesis.pin)" >&2; exit 2; }

for tool in git node npm python3 ssh; do
  command -v "$tool" >/dev/null || { echo "ERROR: $tool is not installed" >&2; exit 1; }
done
major="$(node -p 'process.versions.node.split(".")[0]')"
[[ "$major" -ge 20 ]] || { echo "ERROR: node $(node -v) is too old; the app needs node 20 or newer" >&2; exit 1; }

https_repo="$(sed -E 's#^git@github.com:#https://github.com/#' <<<"$repo")"
if [[ ! -d "$dest/.git" ]]; then
  mkdir -p "$(dirname "$dest")"
  git clone "$repo" "$dest" 2>/dev/null || git clone "$https_repo" "$dest"
fi
git -C "$dest" fetch --quiet origin || git -C "$dest" fetch --quiet "$https_repo" '+refs/heads/*:refs/remotes/origin/*'
git -C "$dest" checkout --quiet --detach "$ref" 2>/dev/null || git -C "$dest" checkout --quiet --detach "origin/$ref"
echo "hep-genesis-agent at $(git -C "$dest" rev-parse --short HEAD) in $dest"

(cd "$dest/desktop" && npm install --no-audit --no-fund)
# An npm that holds back packages' install scripts leaves Electron without
# its program ("Electron uninstall" at the start): run that one step here.
if [[ ! -f "$dest/desktop/node_modules/electron/path.txt" ]]; then
  echo "downloading Electron"
  (cd "$dest/desktop" && node node_modules/electron/install.js)
fi

[[ -f "$dest/desktop/src/main/startHook.ts" ]] || {
  echo "ERROR: this version of the app has no start hook; install a newer one with --ref" >&2; exit 1; }

hook="$HOME/hep-genesis/start-hook.json"
mkdir -p "$(dirname "$hook")"
python3 - "$hook" "$(command -v python3)" "$here/connect.py" "$mode" "$ssh_host" <<'PY'
import json, sys
hook, python, connect, mode, host = sys.argv[1:6]
args = [connect, "--mode", mode] + (["--ssh-host", host] if host else [])
with open(hook, "w", encoding="utf-8") as f:
    json.dump({"command": python, "args": args, "timeoutS": 45}, f, indent=2)
    f.write("\n")
PY
echo "start hook ($mode) written to $hook"
echo
echo "Check the connection:   python3 $here/connect.py --mode $mode${ssh_host:+ --ssh-host $ssh_host} --check"
echo "Start the app:          cd $dest/desktop && npm run dev"
