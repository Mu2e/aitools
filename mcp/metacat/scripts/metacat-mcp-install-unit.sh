#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  metacat-mcp-install-unit.sh [--port <port>] [--host <host>]
                               [--metacat-server-url <url>]
                               [--metacat-auth-server-url <url>]
                               [--no-enable]

Renders a systemd --user unit for metacat-mcp into THIS install's own
share/ directory (share/metacat-mcp/metacat-mcp.service) -- not into
~/.config -- and registers it with `systemctl --user link`, which creates a
symlink in ~/.config/systemd/user pointing back here. So the real unit file
content stays with the installed code; ~/.config only ever holds a pointer
to it.

ExecStart is resolved from this script's own location (same trick
metacat-mcp.sh uses to find its sibling), so no path needs to be hand-edited
or substituted.

--metacat-server-url / --metacat-auth-server-url are optional: metacat.webapi
reads METACAT_SERVER_URL / METACAT_AUTH_SERVER_URL from the environment, so
if given, they're written as Environment= lines in the unit; if omitted, the
service inherits whatever metacat-client's own defaults or ambient
environment provide.

Safe to re-run (e.g. after changing --port, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart/Environment lines.

Examples:
  <venv>/bin/metacat-mcp-install-unit.sh
  <venv>/bin/metacat-mcp-install-unit.sh --port 8002 --no-enable
  <venv>/bin/metacat-mcp-install-unit.sh --metacat-server-url https://metacat.fnal.gov:9443/mu2e_meta_prod/app
USAGE
  exit 2
}

port=8002
host=0.0.0.0
metacat_server_url=""
metacat_auth_server_url=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --metacat-server-url) metacat_server_url="$2"; shift 2 ;;
    --metacat-auth-server-url) metacat_auth_server_url="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/metacat-mcp"
unit_file="$share_dir/metacat-mcp.service"

mkdir -p "$share_dir"

env_lines=""
[[ -n "$metacat_server_url" ]] && env_lines="${env_lines}Environment=METACAT_SERVER_URL=$metacat_server_url"$'\n'
[[ -n "$metacat_auth_server_url" ]] && env_lines="${env_lines}Environment=METACAT_AUTH_SERVER_URL=$metacat_auth_server_url"$'\n'

cat > "$unit_file" <<EOF
[Unit]
Description=metacat-mcp (read-only streamable-HTTP MCP server for Mu2e metacat discovery)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
${env_lines}ExecStart=$script_dir/metacat-mcp.sh --host=$host --port=$port
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
  systemctl --user enable --now metacat-mcp
  systemctl --user status metacat-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now metacat-mcp"
fi
