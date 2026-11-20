#!/usr/bin/env bash
# Usage logging for sandboxed sessions: an $AGENTBOX_POST_HOOK for agentbox
# (claudebox / goosebox).
#
# agentbox runs this on the host, outside the sandbox, when a `run` session
# or an agent ends. It finds the sessions the box wrote since it started --
# in the sandbox home, which the container cannot see past -- and hands each
# to the harness's usual logger, so they land in the same usage.jsonl, in
# the same format, as sessions outside the box:
#
#   claude  transcripts in  $AGENTBOX_HOME/.claude/projects
#           -> ../claude-code/log-session-claude.py
#   goose   sessions in     $AGENTBOX_HOME/.local/share/goose/sessions/sessions.db
#           -> ../goose/log-session.py
#
# Nothing has to run, or be mounted, inside the container for this.
#
# Sessions are picked by time (changed since $AGENTBOX_SINCE), so with two
# boxes running at once each may also log the other's session. That is
# harmless: the loggers keep one line per session.
#
# Never fails the session: always exits 0.

set -uo pipefail

home=${AGENTBOX_HOME:-}
since=${AGENTBOX_SINCE:-0}
tool=${AGENTBOX_TOOL:-claude}
[[ -n "$home" && -d "$home" ]] || { echo "log-usage: AGENTBOX_HOME is not set (run by agentbox?)" >&2; exit 0; }

harness="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
user_root="/exp/${EXPERIMENT:-mu2e}/app/users/$USER"

case "$tool" in
  claude)
    root="$home/.claude/projects"
    [[ -d "$root" ]] || exit 0
    # projects/<project>/<session>.jsonl; deeper files belong to subagents.
    while IFS= read -r f; do
      LOG_PROJECTS_ROOT="$root" \
        python3 "$harness/claude-code/log-session-claude.py" "$(basename "$f" .jsonl)" >/dev/null \
        || echo "log-usage: could not log $(basename "$f")" >&2
    done < <(find "$root" -mindepth 2 -maxdepth 2 -name '*.jsonl' -newermt "@$since" 2>/dev/null)
    ;;
  goose)
    db="$home/.local/share/goose/sessions/sessions.db"
    [[ -f "$db" ]] || exit 0
    # The host's goose log, also when the goose harness is not loaded here.
    : "${LOG_OUTPUT:=${XDG_DATA_HOME:-${GOOSE_ROOT:-$user_root/goose}/data}/goose/log/usage.jsonl}"
    export LOG_OUTPUT
    while IFS= read -r id; do
      [[ -n "$id" ]] || continue
      # XDG_CONFIG_HOME: the logger reads the endpoint from the box's config.
      LOG_DB="$db" XDG_CONFIG_HOME="$home/.config" \
        python3 "$harness/goose/log-session.py" "$id" >/dev/null \
        || echo "log-usage: could not log goose session $id" >&2
    done < <(python3 - "$db" "$since" 2>/dev/null <<'PY'
import sqlite3, sys
db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
# updated_at is UTC text, as SQLite's datetime() writes it.
for (sid,) in db.execute(
        "SELECT id FROM sessions WHERE updated_at >= datetime(?, 'unixepoch')", (int(sys.argv[2]),)):
    print(sid)
PY
)
    ;;
  *)
    echo "log-usage: no usage logger for tool '$tool'" >&2
    ;;
esac
exit 0
