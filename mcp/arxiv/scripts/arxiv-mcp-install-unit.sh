#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  arxiv-mcp-install-unit.sh [--port <port>] [--host <host>]
                             [--page-size <n>] [--delay-seconds <secs>]
                             [--num-retries <n>] [--no-enable]

Renders a systemd --user unit for arxiv-mcp into THIS install's own share/
directory (share/arxiv-mcp/arxiv-mcp.service) -- not into ~/.config -- and
registers it with `systemctl --user link`, which creates a symlink in
~/.config/systemd/user pointing back here. So the real unit file content
stays with the installed code; ~/.config only ever holds a pointer to it.

ExecStart is resolved from this script's own location (same trick
arxiv-mcp.sh uses to find its sibling), so no path needs to be hand-edited
or substituted. Any flag left unset is simply omitted from ExecStart, so
arxiv-mcp falls back to its own built-in defaults (page_size=100,
delay_seconds=3.0, num_retries=3 -- arXiv's own requested rate-limit
etiquette) or the ARXIV_MCP_* env vars, same as running it directly.

Safe to re-run (e.g. after changing --port, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart line.

Examples:
  <venv>/bin/arxiv-mcp-install-unit.sh
  <venv>/bin/arxiv-mcp-install-unit.sh --port 8003 --no-enable
USAGE
  exit 2
}

port=8003
host=0.0.0.0
page_size=""
delay_seconds=""
num_retries=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --page-size) page_size="$2"; shift 2 ;;
    --delay-seconds) delay_seconds="$2"; shift 2 ;;
    --num-retries) num_retries="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/arxiv-mcp"
unit_file="$share_dir/arxiv-mcp.service"

mkdir -p "$share_dir"

exec_start="$script_dir/arxiv-mcp.sh --host=$host --port=$port"
[[ -n "$page_size" ]] && exec_start="$exec_start --page-size=$page_size"
[[ -n "$delay_seconds" ]] && exec_start="$exec_start --delay-seconds=$delay_seconds"
[[ -n "$num_retries" ]] && exec_start="$exec_start --num-retries=$num_retries"

cat > "$unit_file" <<EOF
[Unit]
Description=arxiv-mcp (read-only streamable-HTTP MCP server for arXiv paper search)
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
  systemctl --user enable --now arxiv-mcp
  systemctl --user status arxiv-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now arxiv-mcp"
fi
