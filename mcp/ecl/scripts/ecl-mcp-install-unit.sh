#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  ecl-mcp-install-unit.sh [--port <port>] [--host <host>] [--env-file <path>]
                           [--no-enable]

Renders a systemd --user unit for ecl-mcp into THIS install's own share/
directory (share/ecl-mcp/ecl-mcp.service) -- not into ~/.config -- and
registers it with `systemctl --user link`, which creates a symlink in
~/.config/systemd/user pointing back here. So the real unit file content
stays with the installed code; ~/.config only ever holds a pointer to it.

--env-file points at a file of KEY=VALUE lines (ECL_URL, ECL_USER_NAME,
ECL_PASSWORD, MIKEY_KEYS_FILE, and optionally ECL_MCP_READ_ONLY /
MIKEY_SERVER_URL) that becomes this unit's EnvironmentFile=. Credentials
never go into ExecStart or any --flag here: unlike a bind host or port,
they'd then be visible to any user on the box via `ps`, and would get
baked in plaintext into the unit file this script renders (which anyone
who can run `systemctl --user cat` on this account can read). An
env-file's own filesystem permissions are the actual protection --
this script does not chmod it for you; set it to 0600 yourself, owned by
the account that runs this unit (the same account mikey's keys file lives
in -- see AUTHPLAN.md and mikey's README for why that's the security
boundary this whole design rests on).

ExecStart itself is resolved from this script's own location (same trick
ecl-mcp.sh uses to find its sibling), so no path needs to be hand-edited or
substituted.

Safe to re-run (e.g. after changing --port, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart/EnvironmentFile.

Examples:
  <venv>/bin/ecl-mcp-install-unit.sh --env-file /path/to/ecl-mcp.env
  <venv>/bin/ecl-mcp-install-unit.sh --port 8005 --env-file /path/to/ecl-mcp.env --no-enable
USAGE
  exit 2
}

port=8005
host=0.0.0.0
env_file=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --env-file) env_file="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

if [[ -z "$env_file" ]]; then
  echo "WARNING: no --env-file given -- ecl-mcp will start with whatever" >&2
  echo "         ECL_URL/ECL_USER_NAME/ECL_PASSWORD/MIKEY_KEYS_FILE (if any)" >&2
  echo "         are already in this systemd --user session's environment," >&2
  echo "         which is almost never what you want for a real deployment." >&2
elif [[ ! -f "$env_file" ]]; then
  echo "ERROR: --env-file not found: $env_file" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/ecl-mcp"
unit_file="$share_dir/ecl-mcp.service"

mkdir -p "$share_dir"

exec_start="$script_dir/ecl-mcp.sh --host=$host --port=$port"

environment_line=""
[[ -n "$env_file" ]] && environment_line="EnvironmentFile=$env_file"

cat > "$unit_file" <<EOF
[Unit]
Description=ecl-mcp (streamable-HTTP MCP server for the Fermilab ECL logbook, mikey-authenticated)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
$environment_line
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
  systemctl --user enable --now ecl-mcp
  systemctl --user status ecl-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now ecl-mcp"
fi
