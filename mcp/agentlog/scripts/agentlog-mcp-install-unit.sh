#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  agentlog-mcp-install-unit.sh --mikey-keys-file <path> --port <port>
                               --db-host <host> --db-port <port> --db-name <name>
                               [--krb5-ccname <ccname>] [--host <bind-address>]
                               [--db-schema <schema>] [--write-role <role>]
                               [--env-file <path>] [--no-enable]

  agentlog-mcp-install-unit.sh --mikey-keys-file <path> --env-file <path>
                               [--port <port>] [...]

Renders a systemd --user unit for agentlog-mcp into THIS install's own share/
directory (share/agentlog-mcp/agentlog-mcp.service) -- not into ~/.config --
and registers it with `systemctl --user link`, which creates a symlink in
~/.config/systemd/user pointing back here. So the real unit content stays with
the installed code; ~/.config only ever holds a pointer to it.

--mikey-keys-file is REQUIRED. agentlog-mcp will not start without it: the
mikey key's name is recorded as every report's author, which is the only
accountability an append-only log has. The path itself is not a secret (the
keys file's own permissions are what protect it), so it goes in the unit as a
plain Environment= line.

THE ENDPOINT IS REQUIRED AND IS NOT IN THE PACKAGE. agentlog-mcp ships no
database host, port or name and no bind port -- deployment facts do not belong
in a git repository. Supply them here with --db-host/--db-port/--db-name/--port
(they are written into the unit), or put them in an --env-file as

    AGENTLOG_MCP_DB_HOST=...
    AGENTLOG_MCP_DB_PORT=...
    AGENTLOG_MCP_DB_NAME=...
    AGENTLOG_MCP_PORT=...

which keeps them out of the unit as well. This script checks that each value
is available from one source or the other and refuses to render a unit that
would fail at startup.

--krb5-ccname sets KRB5CCNAME, the Kerberos credential cache used for the
database connection (e.g. FILE:/tmp/krb5cc_18199_auto). Pass it whenever the
renewed cache is NOT the default location for this account, which is the usual
case when a cron/keytab job maintains it.

This matters more than it looks. A `systemd --user` service gets a minimal
environment and does NOT inherit KRB5CCNAME from your login shell, so it reads
the DEFAULT cache. If a renewal job is faithfully refreshing some other cache,
`klist` in your shell shows a perfectly valid ticket while the service reads a
stale one and every database connection fails with "GSSAPI continuation error
... Ticket expired". That has happened here with memory-mcp: the default cache
sat expired for a week while a healthy renewed ticket lived in an _auto cache
the service never saw. This script therefore validates the cache with
`klist -s` at install time, which catches an expired or missing ticket before
the unit is written rather than at the next restart.

ExecStart is resolved from this script's own location (the same trick
agentlog-mcp.sh uses to find its sibling), so no path needs hand-editing.

Safe to re-run (after changing a flag, or after a redeploy): re-linking and
re-enabling an already-installed unit is a no-op other than picking up the new
ExecStart/Environment lines. It does NOT restart a running process -- run
`systemctl --user restart agentlog-mcp` for that.

Examples:
  <venv>/bin/agentlog-mcp-install-unit.sh --mikey-keys-file /path/to/mikey/keys \
      --port <port> --db-host <host> --db-port <port> --db-name <db> \
      --krb5-ccname FILE:/tmp/krb5cc_18199_auto
  <venv>/bin/agentlog-mcp-install-unit.sh --mikey-keys-file /path/to/keys \
      --env-file /path/to/agentlog.env --no-enable
USAGE
  exit 2
}

port=""
host=0.0.0.0
mikey_keys_file=""
db_host=""
db_port=""
db_name=""
db_schema=""
write_role=""
env_file=""
krb5_ccname=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --mikey-keys-file) mikey_keys_file="$2"; shift 2 ;;
    --krb5-ccname) krb5_ccname="$2"; shift 2 ;;
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
  echo "ERROR: --mikey-keys-file is required (agentlog-mcp will not start without it)" >&2
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

# --- Endpoint completeness ---------------------------------------------------
# Each of these four has to come from a flag or from the env-file. Checking now
# beats a unit that renders cleanly and then dies on every start with
# "no database endpoint configured".
in_env_file() {
  [[ -n "$env_file" ]] && grep -Eq "^[[:space:]]*$1[[:space:]]*=" "$env_file"
}
missing=()
[[ -n "$db_port"  ]] || in_env_file AGENTLOG_MCP_DB_PORT || missing+=("--db-port  (or AGENTLOG_MCP_DB_PORT in --env-file)")
[[ -n "$db_host"  ]] || in_env_file AGENTLOG_MCP_DB_HOST || missing+=("--db-host  (or AGENTLOG_MCP_DB_HOST in --env-file)")
[[ -n "$db_name"  ]] || in_env_file AGENTLOG_MCP_DB_NAME || missing+=("--db-name  (or AGENTLOG_MCP_DB_NAME in --env-file)")
[[ -n "$port"     ]] || in_env_file AGENTLOG_MCP_PORT    || missing+=("--port     (or AGENTLOG_MCP_PORT in --env-file)")
if [[ ${#missing[@]} -gt 0 ]]; then
  echo "ERROR: the deployment endpoint is incomplete -- agentlog-mcp ships no defaults for:" >&2
  for m in "${missing[@]}"; do echo "  $m" >&2; done
  exit 2
fi

if [[ -n "$env_file" ]]; then
  # Anyone who can read the env file can read the endpoint; that is fine, but a
  # world-readable file is worth a word, since it is the one place the
  # deployment's connection details are written down.
  perms=$(stat -c '%a' "$env_file" 2>/dev/null || echo "")
  if [[ -n "$perms" && "${perms: -1}" != "0" ]]; then
    echo "NOTE: $env_file is mode $perms -- consider chmod 640 if it holds the endpoint." >&2
  fi
fi

# --- Kerberos credential cache -----------------------------------------------
# The service authenticates to Postgres with GSSAPI, so a cache the service can
# actually read, holding an unexpired ticket, is a hard requirement. Validate it
# here: an expired ticket found now is a clear message, while the same thing
# found at runtime is a wall of psycopg GSSAPI text in the journal.
if [[ -n "$krb5_ccname" ]]; then
  if ! klist -s -c "$krb5_ccname" 2>/dev/null; then
    echo "ERROR: no valid Kerberos ticket in $krb5_ccname" >&2
    echo "  Either the cache does not exist, or its ticket has expired." >&2
    echo "  Check with:  klist -c $krb5_ccname" >&2
    echo "  agentlog-mcp cannot reach the database without one." >&2
    exit 2
  fi
  echo "Kerberos: valid ticket in $krb5_ccname"
else
  # No explicit cache: the service will read the DEFAULT one. Check whether that
  # is actually viable, because this is the exact case that broke memory-mcp in
  # production -- a renewal job kept a non-default cache fresh while the default
  # went stale.
  if klist -s 2>/dev/null; then
    default_cc=$(klist 2>/dev/null | sed -n '1s/^Ticket cache: //p')
    if [[ -n "${KRB5CCNAME:-}" ]]; then
      echo "WARNING: no --krb5-ccname given." >&2
      echo "  Your shell has KRB5CCNAME=$KRB5CCNAME, but a systemd --user service" >&2
      echo "  does NOT inherit it -- the service will read the default cache," >&2
      echo "  which may be a different (and staler) one than you just checked." >&2
      echo "  Pass --krb5-ccname \"$KRB5CCNAME\" to pin the cache the service uses." >&2
    else
      echo "Kerberos: no --krb5-ccname given; service will use the default cache ($default_cc)"
    fi
  else
    echo "WARNING: no --krb5-ccname given and the default Kerberos cache has no" >&2
    echo "  valid ticket. agentlog-mcp will fail to reach the database at startup." >&2
    echo "  If a renewal job maintains a non-default cache, pass its path:" >&2
    echo "    --krb5-ccname FILE:/tmp/krb5cc_<uid>_auto" >&2
  fi
fi

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/agentlog-mcp"
unit_file="$share_dir/agentlog-mcp.service"

mkdir -p "$share_dir"

exec_start="$script_dir/agentlog-mcp.sh --host=$host"
[[ -n "$port" ]]       && exec_start="$exec_start --port=$port"
[[ -n "$db_schema" ]]  && exec_start="$exec_start --db-schema=$db_schema"
[[ -n "$write_role" ]] && exec_start="$exec_start --write-role=$write_role"

# The endpoint goes in as Environment= rather than on the command line so it
# does not show up in every `ps` listing on the host.
environment_lines="Environment=MIKEY_KEYS_FILE=$mikey_keys_file"
[[ -n "$db_host" ]] && environment_lines="$environment_lines
Environment=AGENTLOG_MCP_DB_HOST=$db_host"
[[ -n "$db_port" ]] && environment_lines="$environment_lines
Environment=AGENTLOG_MCP_DB_PORT=$db_port"
[[ -n "$db_name" ]] && environment_lines="$environment_lines
Environment=AGENTLOG_MCP_DB_NAME=$db_name"
[[ -n "$krb5_ccname" ]] && environment_lines="$environment_lines
Environment=KRB5CCNAME=$krb5_ccname"
[[ -n "$env_file" ]] && environment_lines="$environment_lines
EnvironmentFile=$env_file"

cat > "$unit_file" <<EOF
[Unit]
Description=agentlog-mcp (agent investigation log MCP server, Postgres-backed)
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
echo "  auth:     mikey  (keys file: $mikey_keys_file)"
if [[ -n "$krb5_ccname" ]]; then
  echo "  krb5:     $krb5_ccname"
else
  echo "  krb5:     (default cache -- not pinned; see --krb5-ccname)"
fi
[[ -n "$env_file" ]] && echo "  env-file: $env_file"

mkdir -p "$HOME/.config/systemd/user"
systemctl --user link --force "$unit_file"
systemctl --user daemon-reload

if [[ $do_enable -eq 1 ]]; then
  systemctl --user enable --now agentlog-mcp
  systemctl --user status agentlog-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now agentlog-mcp"
fi
