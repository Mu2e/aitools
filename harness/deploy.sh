#!/usr/bin/env bash
# Deploy the claude-code and goose harnesses to a shared, read-only location
# (CVMFS), from which users source setup.sh / env.sh without cloning.
#
#   harness/deploy.sh DEST
#
# Copies the *committed* tree (git archive of HEAD -- uncommitted changes
# are not deployed, and file modes come from git, not from your umask) to
# DEST and writes DEST/VERSION with the commit, which the usage log records
# as harness_version (a deployed copy has no .git to ask).
#
# Deployed: README.md, claude-code/, claudebox/, goose/, shared/, usage/. Not the Slack
# bot (a service with its own install). Files already in DEST are
# overwritten; files no longer in the repository are not removed -- deploy
# into an empty directory for a clean copy.
#
# This only fills DEST. Opening and publishing the CVMFS transaction is up
# to whoever runs it.

set -euo pipefail

if [[ $# -ne 1 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" >&2
  exit 2
fi

dest="$1"
harness_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(git -C "$harness_dir" rev-parse --show-toplevel)"
prefix="$(git -C "$harness_dir" rev-parse --show-prefix)"   # "harness/"
commit="$(git -C "$repo" rev-parse --short=7 HEAD)"

parts=(README.md claude-code claudebox goose shared usage)

if [[ -n "$(git -C "$repo" status --porcelain -- "${parts[@]/#/$prefix}")" ]]; then
  echo "Note: uncommitted changes under $prefix are NOT deployed (deploying $commit):" >&2
  git -C "$repo" status --short -- "${parts[@]/#/$prefix}" >&2
fi

mkdir -p "$dest"
# Paths in the archive are relative to the harness directory.
( cd "$harness_dir" && git archive --format=tar HEAD -- "${parts[@]}" ) | tar -x -C "$dest"
printf '%s\n' "$commit" > "$dest/VERSION"
chmod -R u+rwX,go+rX,go-w "$dest"

echo "Deployed aitools harness $commit to $dest"
echo "Users:  . $dest/claude-code/setup.sh   or   . $dest/goose/setup.sh"
