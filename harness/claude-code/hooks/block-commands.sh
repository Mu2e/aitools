#!/usr/bin/env bash
# PreToolUse hook for Claude Code's Bash tool: blocks ssh/scp/sftp/rsync/
# kinit/sudo/su, also behind wrappers, sh -c '...', $(...) etc. The logic
# is shared with the goose harness: see ../../shared/block_commands.py.
# A heuristic guard rail, not a sandbox.
exec python3 -I "$(dirname "${BASH_SOURCE[0]}")/../../shared/block_commands.py" "Claude Code"
