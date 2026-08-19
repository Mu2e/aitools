#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  registry-mcp-install-unit.sh --registry <path> [--port <port>] [--host <host>] [--no-enable]

Renders a systemd --user unit for registry-mcp into THIS install's own
share/ directory (share/registry-mcp/registry-mcp.service) -- not into
~/.config -- and registers it with `systemctl --user link`, which creates
a symlink in ~/.config/systemd/user pointing back here. So the real unit
file content stays with the installed code; ~/.config only ever holds a
pointer to it.

ExecStart is resolved from this script's own location (same trick
registry-mcp.sh uses to find its sibling), so no path needs to be
hand-edited or substituted.

Safe to re-run (e.g. after changing --port/--registry, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart line.

Examples:
  <venv>/bin/registry-mcp-install-unit.sh --registry /path/to/ports.json
  <venv>/bin/registry-mcp-install-unit.sh --registry /path/to/ports.json --port 8001 --no-enable
USAGE
  exit 2
}

port=8000
host=0.0.0.0
registry_file=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --registry) registry_file="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

if [[ -z "$registry_file" ]]; then
  echo "ERROR: --registry is required" >&2
  usage
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/registry-mcp"
unit_file="$share_dir/registry-mcp.service"

mkdir -p "$share_dir"

cat > "$unit_file" <<EOF
[Unit]
Description=registry-mcp (trivial HTTP MCP server with a live registry endpoint)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$script_dir/registry-mcp.sh --host=$host --port=$port --registry=$registry_file
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
  systemctl --user enable --now registry-mcp
  systemctl --user status registry-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now registry-mcp"
fi
