#!/usr/bin/env bash
# PreToolUse hook for goose's shell tool (see ../hooks/hooks.json): blocks
# ssh/scp/sftp/rsync/kinit/sudo/su, also behind wrappers, sh -c '...',
# $(...) etc. The logic is shared with the claude-code harness: see
# ../../../shared/block_commands.py. A heuristic guard rail, not a sandbox.
exec python3 -I "$(dirname "${BASH_SOURCE[0]}")/../../../shared/block_commands.py" "goose"
