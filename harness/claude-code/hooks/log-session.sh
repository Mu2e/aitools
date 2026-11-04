#!/usr/bin/env bash
# Stop + UserPromptSubmit hook -- log usage summary for the current session.
#
# Claude Code hook payload on stdin:
#   { "hook_event_name": "Stop"|"UserPromptSubmit", "session_id": "...", ... }
#
# On Stop the final assistant message is not yet in the transcript when the
# hook fires, so the logger runs detached after a short delay (setsid: it
# must outlive `claude -p`, which exits right after the hook).
#
# Non-fatal: exits 0 on any error so Claude Code is never blocked.

set -euo pipefail

payload="$(cat)"

read -r session_id event < <(printf '%s' "$payload" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(d.get('session_id', ''), d.get('hook_event_name', ''))
" 2>/dev/null || true) || true

if [[ -z "$session_id" ]]; then
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log_script="$script_dir/../log-session-claude.py"

if [[ ! -f "$log_script" ]]; then
  exit 0
fi

if [[ "$event" == "Stop" ]]; then
  setsid bash -c 'sleep 2; exec python3 "$0" "$1"' "$log_script" "$session_id" \
    </dev/null >/dev/null 2>&1 &
  exit 0
fi

if ! python3 "$log_script" "$session_id" >/dev/null 2>&1; then
  echo "log-session.sh: warning: log-session-claude.py failed for session $session_id" >&2
fi

exit 0
