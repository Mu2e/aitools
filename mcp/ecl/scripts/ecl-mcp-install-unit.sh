#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  ecl-mcp-install-unit.sh --env-file <path> | --no-auth
                           [--port <port>] [--host <host>] [--no-enable]

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

--env-file is REQUIRED (pass --no-auth to deliberately run without it).
This script also inspects the file -- key names only, never values -- and
refuses to proceed on the two mistakes that have actually happened here:

  * MIKEY_KEYS_FILE missing, which starts the server wide open. It runs
    happily with auth off, so nothing surfaces the regression until someone
    probes the port.
  * `export KEY=VALUE` lines. systemd's EnvironmentFile= is NOT a shell:
    it wants plain KEY=VALUE. The `export` form loads without error and
    silently never sets the variable -- which is exactly how ecl-mcp ran
    unauthenticated while its env file looked correct.

Safe to re-run (e.g. after changing --port, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart/EnvironmentFile.

Examples:
  <venv>/bin/ecl-mcp-install-unit.sh --env-file /path/to/ecl-mcp.env
  <venv>/bin/ecl-mcp-install-unit.sh --port 8005 --env-file /path/to/ecl-mcp.env --no-enable
  <venv>/bin/ecl-mcp-install-unit.sh --no-auth      # deliberately open
USAGE
  exit 2
}

port=8005
host=0.0.0.0
env_file=""
no_auth=0
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --env-file) env_file="$2"; shift 2 ;;
    --no-auth) no_auth=1; shift ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

if [[ -n "$env_file" && $no_auth -eq 1 ]]; then
  echo "ERROR: --env-file and --no-auth are mutually exclusive" >&2
  exit 2
fi
if [[ -z "$env_file" && $no_auth -eq 0 ]]; then
  echo "ERROR: no --env-file given." >&2
  echo "  ecl-mcp needs ECL_URL/ECL_USER_NAME/ECL_PASSWORD, and MIKEY_KEYS_FILE" >&2
  echo "  for auth. Without the file it inherits whatever happens to be in the" >&2
  echo "  systemd --user session environment -- in practice, nothing, which" >&2
  echo "  starts an unauthenticated server that cannot reach ECL." >&2
  echo "  Pass --env-file <path>, or --no-auth to do this deliberately." >&2
  exit 2
fi

# Inspect the env file: KEY NAMES ONLY, never values -- this file holds the
# ECL password, and this script's output is not a safe place for it.
if [[ -n "$env_file" ]]; then
  if [[ ! -f "$env_file" ]]; then
    echo "ERROR: --env-file not found: $env_file" >&2
    exit 2
  fi

  # systemd's EnvironmentFile= is not a shell. `export KEY=VALUE` parses as a
  # variable literally named "export KEY", so the real variable is never set --
  # the file loads clean and the setting silently vanishes.
  if grep -qE '^[[:space:]]*export[[:space:]]+[A-Za-z_]' "$env_file"; then
    echo "ERROR: $env_file has 'export KEY=VALUE' lines." >&2
    echo "  systemd EnvironmentFile= is not a shell -- it needs plain KEY=VALUE." >&2
    echo "  The export form loads without error but never sets the variable." >&2
    echo "  Offending line numbers: $(grep -nE '^[[:space:]]*export[[:space:]]+[A-Za-z_]' "$env_file" | cut -d: -f1 | tr '\n' ' ')" >&2
    exit 2
  fi

  env_keys=$(grep -oE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=' "$env_file" \
             | tr -d ' \t=' | sort -u)

  if ! grep -qx "MIKEY_KEYS_FILE" <<<"$env_keys"; then
    echo "ERROR: $env_file does not set MIKEY_KEYS_FILE -- ecl-mcp would start" >&2
    echo "  with authentication disabled, open to anyone who can reach port $port." >&2
    echo "  Add it, or pass --no-auth if that is genuinely intended." >&2
    exit 2
  fi

  # Confirm the keys file it points at actually exists. Reading one value is
  # justified here; it is a path, not a secret, and a wrong path is another
  # silent way to end up unauthenticated.
  keys_path=$(grep -E '^[[:space:]]*MIKEY_KEYS_FILE[[:space:]]*=' "$env_file" \
              | tail -1 | cut -d= -f2- | sed 's/^[[:space:]]*//; s/[[:space:]]*$//; s/^"//; s/"$//')
  if [[ ! -f "$keys_path" ]]; then
    echo "ERROR: MIKEY_KEYS_FILE in $env_file points at a file that does not exist:" >&2
    echo "  $keys_path" >&2
    exit 2
  fi

  for required in ECL_URL ECL_USER_NAME ECL_PASSWORD; do
    grep -qx "$required" <<<"$env_keys" || \
      echo "WARNING: $env_file does not set $required -- ecl-mcp --check will fail." >&2
  done
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
if [[ -n "$env_file" ]]; then
  echo "  env-file: $env_file"
  echo "  auth:     mikey  (MIKEY_KEYS_FILE -> $keys_path)"
else
  echo "  auth:     DISABLED -- this server is open to anyone who can reach port $port"
fi

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
