#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  inspirehep-mcp-install-unit.sh [--port <port>] [--host <host>]
                                  [--requests-per-second <n>]
                                  [--cache-ttl-seconds <secs>]
                                  [--cache-max-entries <n>] [--no-enable]

Renders a systemd --user unit for inspirehep-mcp into THIS install's own
share/ directory (share/inspirehep-mcp/inspirehep-mcp.service) -- not into
~/.config -- and registers it with `systemctl --user link`, which creates a
symlink in ~/.config/systemd/user pointing back here. So the real unit file
content stays with the installed code; ~/.config only ever holds a pointer
to it.

ExecStart is resolved from this script's own location (same trick
inspirehep-mcp.sh uses to find its sibling), so no path needs to be
hand-edited or substituted. Any flag left unset is simply omitted from
ExecStart, so inspirehep-mcp falls back to its own built-in defaults
(requests_per_second=2.0, comfortably under INSPIRE-HEP's documented 15/5s
per-IP limit) or the INSPIREHEP_MCP_* env vars, same as running it directly.

Safe to re-run (e.g. after changing --port, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart line.

Examples:
  <venv>/bin/inspirehep-mcp-install-unit.sh
  <venv>/bin/inspirehep-mcp-install-unit.sh --port 8004 --no-enable
USAGE
  exit 2
}

port=8004
host=0.0.0.0
requests_per_second=""
cache_ttl_seconds=""
cache_max_entries=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --requests-per-second) requests_per_second="$2"; shift 2 ;;
    --cache-ttl-seconds) cache_ttl_seconds="$2"; shift 2 ;;
    --cache-max-entries) cache_max_entries="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/inspirehep-mcp"
unit_file="$share_dir/inspirehep-mcp.service"

mkdir -p "$share_dir"

exec_start="$script_dir/inspirehep-mcp.sh --host=$host --port=$port"
[[ -n "$requests_per_second" ]] && exec_start="$exec_start --requests-per-second=$requests_per_second"
[[ -n "$cache_ttl_seconds" ]] && exec_start="$exec_start --cache-ttl-seconds=$cache_ttl_seconds"
[[ -n "$cache_max_entries" ]] && exec_start="$exec_start --cache-max-entries=$cache_max_entries"

cat > "$unit_file" <<EOF
[Unit]
Description=inspirehep-mcp (read-only streamable-HTTP MCP server for INSPIRE-HEP search)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$exec_start
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF

echo "Wrote unit: $unit_file"

mkdir -p "$HOME/.config/systemd/user"
systemctl --user link --force "$unit_file"
systemctl --user daemon-reload

if [[ $do_enable -eq 1 ]]; then
  systemctl --user enable --now inspirehep-mcp
  systemctl --user status inspirehep-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now inspirehep-mcp"
fi
