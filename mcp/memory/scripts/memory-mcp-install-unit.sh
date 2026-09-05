#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  memory-mcp-install-unit.sh --mikey-keys-file <path> [--port <port>] [--host <host>]
                             [--db-host <host>] [--db-port <port>] [--db-name <name>]
                             [--db-schema <schema>] [--write-role <role>]
                             [--env-file <path>] [--no-enable]

Renders a systemd --user unit for memory-mcp into THIS install's own share/
directory (share/memory-mcp/memory-mcp.service) -- not into ~/.config -- and
registers it with `systemctl --user link`, which creates a symlink in
~/.config/systemd/user pointing back here. So the real unit content stays with
the installed code; ~/.config only ever holds a pointer to it.

--mikey-keys-file is REQUIRED. memory-mcp will not start without it: the mikey
key's name is the document owner, and owner is the only thing separating one
caller's documents from another's. The path itself is not a secret (the keys
file's own permissions are what protect it), so it goes in the unit as a plain
Environment= line.

--env-file is optional, for anything site-specific the service needs in its
environment -- most likely KRB5CCNAME, if the Kerberos credential cache used
for the database connection is not in the default location. It becomes the
unit's EnvironmentFile=. Note that systemd parses that file as plain
KEY=VALUE lines: do NOT write `export KEY=VALUE`, which systemd does not
understand (it is not a shell) and which silently fails to set the variable.

ExecStart is resolved from this script's own location (same trick
memory-mcp.sh uses to find its sibling), so no path needs hand-editing.

Safe to re-run (after changing a flag, or after a redeploy): re-linking and
re-enabling an already-installed unit is a no-op other than picking up the new
ExecStart/Environment lines. It does NOT restart a running process -- run
`systemctl --user restart memory-mcp` for that.

Examples:
  <venv>/bin/memory-mcp-install-unit.sh --mikey-keys-file /path/to/mikey/keys
  <venv>/bin/memory-mcp-install-unit.sh --port 8007 --mikey-keys-file /path/to/keys --no-enable
USAGE
  exit 2
}

port=8007
host=0.0.0.0
mikey_keys_file=""
db_host=""
db_port=""
db_name=""
db_schema=""
write_role=""
env_file=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --mikey-keys-file) mikey_keys_file="$2"; shift 2 ;;
    --db-host) db_host="$2"; shift 2 ;;
    --db-port) db_port="$2"; shift 2 ;;
    --db-name) db_name="$2"; shift 2 ;;
    --db-schema) db_schema="$2"; shift 2 ;;
    --write-role) write_role="$2"; shift 2 ;;
    --env-file) env_file="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

if [[ -z "$mikey_keys_file" ]]; then
  echo "ERROR: --mikey-keys-file is required (memory-mcp will not start without it)" >&2
  usage
fi
if [[ ! -f "$mikey_keys_file" ]]; then
  echo "ERROR: mikey keys file not found: $mikey_keys_file" >&2
  exit 2
fi
if [[ -n "$env_file" && ! -f "$env_file" ]]; then
  echo "ERROR: --env-file not found: $env_file" >&2
  exit 2
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/memory-mcp"
unit_file="$share_dir/memory-mcp.service"

mkdir -p "$share_dir"

exec_start="$script_dir/memory-mcp.sh --host=$host --port=$port"
[[ -n "$db_host" ]]    && exec_start="$exec_start --db-host=$db_host"
[[ -n "$db_port" ]]    && exec_start="$exec_start --db-port=$db_port"
[[ -n "$db_name" ]]    && exec_start="$exec_start --db-name=$db_name"
[[ -n "$db_schema" ]]  && exec_start="$exec_start --db-schema=$db_schema"
[[ -n "$write_role" ]] && exec_start="$exec_start --write-role=$write_role"

environment_lines="Environment=MIKEY_KEYS_FILE=$mikey_keys_file"
[[ -n "$env_file" ]] && environment_lines="$environment_lines
EnvironmentFile=$env_file"

cat > "$unit_file" <<EOF
[Unit]
Description=memory-mcp (persistent project-memory MCP server, Postgres-backed)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
$environment_lines
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
  systemctl --user enable --now memory-mcp
  systemctl --user status memory-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now memory-mcp"
fi
