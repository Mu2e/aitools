#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  dqm-mcp-install-unit.sh [--port <port>] [--host <host>] [--qe-base-url <url>]
                           [--qe-dbname <name>] [--qe-timeout-seconds <secs>]
                           [--no-enable]

Renders a systemd --user unit for dqm-mcp into THIS install's own share/
directory (share/dqm-mcp/dqm-mcp.service) -- not into ~/.config -- and
registers it with `systemctl --user link`, which creates a symlink in
~/.config/systemd/user pointing back here. So the real unit file content
stays with the installed code; ~/.config only ever holds a pointer to it.

ExecStart is resolved from this script's own location (same trick
dqm-mcp.sh uses to find its sibling), so no path needs to be hand-edited or
substituted. Any QE flag left unset is simply omitted from ExecStart, so
dqm-mcp falls back to its own built-in defaults (nocache endpoint,
mu2e_dqm_prd, 30s timeout) or the DQM_QE_* env vars, same as running it
directly.

Safe to re-run (e.g. after changing --port, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart line.

Examples:
  <venv>/bin/dqm-mcp-install-unit.sh
  <venv>/bin/dqm-mcp-install-unit.sh --port 8001 --no-enable
USAGE
  exit 2
}

port=8001
host=0.0.0.0
qe_base_url=""
qe_dbname=""
qe_timeout_seconds=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --qe-base-url) qe_base_url="$2"; shift 2 ;;
    --qe-dbname) qe_dbname="$2"; shift 2 ;;
    --qe-timeout-seconds) qe_timeout_seconds="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/dqm-mcp"
unit_file="$share_dir/dqm-mcp.service"

mkdir -p "$share_dir"

exec_start="$script_dir/dqm-mcp.sh --host=$host --port=$port"
[[ -n "$qe_base_url" ]] && exec_start="$exec_start --qe-base-url=$qe_base_url"
[[ -n "$qe_dbname" ]] && exec_start="$exec_start --qe-dbname=$qe_dbname"
[[ -n "$qe_timeout_seconds" ]] && exec_start="$exec_start --qe-timeout-seconds=$qe_timeout_seconds"

cat > "$unit_file" <<EOF
[Unit]
Description=dqm-mcp (read-only streamable-HTTP MCP server for Mu2e DQM Query Engine access)
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
  systemctl --user enable --now dqm-mcp
  systemctl --user status dqm-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now dqm-mcp"
fi
