#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  memory-mcp-install-unit.sh --mikey-keys-file <path> [--krb5-ccname <ccname>]
                             [--port <port>] [--host <host>]
                             [--db-host <host>] [--db-port <port>] [--db-name <name>]
                             [--db-schema <schema>] [--write-role <role>]
                             [--env-file <path>] [--no-enable]
                             [--no-krb5-check]

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

--krb5-ccname sets KRB5CCNAME, the Kerberos credential cache used for the
database connection (e.g. FILE:/tmp/krb5cc_18199_auto). Pass it whenever the
renewed cache is NOT the default location for this account, which is the
usual case when a cron/keytab job maintains it.

This matters more than it looks, and it has now bitten twice. A `systemd --user`
service gets a minimal environment and does NOT inherit KRB5CCNAME from your
login shell, so it reads the DEFAULT cache. If a renewal job is faithfully
refreshing some other cache, `klist` in your shell shows a perfectly valid
ticket while the service reads a stale or empty one and every database
connection fails:

  GSSAPI continuation error: No credentials were supplied ...
  No Kerberos credentials available (default cache: FILE:/tmp/krb5cc_18199)

memory-mcp hit it with an expired default cache while a healthy renewed ticket
lived in /tmp/krb5cc_18199_auto; agentlog-mcp then hit it on its first start,
because this check honored KRB5CCNAME and so validated the shell's cache rather
than the service's.

So the check now looks at what the SERVICE will read:
  * with --krb5-ccname: that cache must hold a valid ticket, or this exits 2.
  * without it: the DEFAULT cache is checked with KRB5CCNAME cleared. No valid
    ticket there is an ERROR, not a warning -- a unit that cannot authenticate
    is not worth writing. If your shell has a usable ticket elsewhere, the
    error names the --krb5-ccname value to pass.
  * --no-krb5-check skips all of it, for installing a unit before the ticket
    exists. The service will fail every connection until one does.

--env-file is optional, for any other site-specific variables. It becomes the
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
  <venv>/bin/memory-mcp-install-unit.sh --mikey-keys-file /path/to/mikey/keys \
      --krb5-ccname FILE:/tmp/krb5cc_18199_auto
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
krb5_ccname=""
do_enable=1
do_krb5_check=1

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
    --no-krb5-check) do_krb5_check=0; shift ;;
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

# --- Kerberos credential cache -----------------------------------------------
# The service authenticates to Postgres with GSSAPI, so a cache the service can
# actually read, holding an unexpired ticket, is a hard requirement. Validate it
# here: an expired ticket found now is a clear message, while the same thing
# found at runtime is a wall of psycopg GSSAPI text in the journal.
if [[ $do_krb5_check -eq 0 ]]; then
  echo "Kerberos: --no-krb5-check given; not validating any credential cache." >&2
  echo "  The service will fail every database connection until a ticket exists" >&2
  echo "  in ${krb5_ccname:-the default cache}." >&2
elif [[ -n "$krb5_ccname" ]]; then
  if ! klist -s -c "$krb5_ccname" 2>/dev/null; then
    echo "ERROR: no valid Kerberos ticket in $krb5_ccname" >&2
    echo "  Either the cache does not exist, or its ticket has expired." >&2
    echo "  Check with:  klist -c $krb5_ccname" >&2
    echo "  memory-mcp cannot reach the database without one." >&2
    exit 2
  fi
  echo "Kerberos: valid ticket in $krb5_ccname"
else
  # No explicit cache: the service will read the DEFAULT one. Check THAT cache,
  # with KRB5CCNAME cleared -- `klist -s` honors KRB5CCNAME, so checking it with
  # this shell's environment intact validates the cache the service will NOT be
  # using. That bug let an agentlog-mcp install through whose service then failed
  # every connection with "No Kerberos credentials available (default cache:
  # FILE:/tmp/krb5cc_18199)" while a perfectly good ticket sat in
  # /tmp/krb5cc_18199_auto -- and this script reported success. Check what the
  # service reads, not what you read.
  if env -u KRB5CCNAME klist -s 2>/dev/null; then
    default_cc=$(env -u KRB5CCNAME klist 2>/dev/null | sed -n '1s/^Ticket cache: //p')
    echo "Kerberos: no --krb5-ccname given; service will use the default cache ($default_cc),"
    echo "          which currently holds a valid ticket."
    if [[ -n "${KRB5CCNAME:-}" && "${KRB5CCNAME#FILE:}" != "${default_cc#FILE:}" ]]; then
      echo "WARNING: your shell uses a DIFFERENT cache (KRB5CCNAME=$KRB5CCNAME)." >&2
      echo "  The service cannot see it. If that is the one a renewal job keeps" >&2
      echo "  fresh, the default cache will go stale and every connection will" >&2
      echo "  fail -- pass --krb5-ccname \"$KRB5CCNAME\" to pin the right one." >&2
    fi
  else
    echo "ERROR: the default Kerberos cache has no valid ticket, and no" >&2
    echo "  --krb5-ccname was given. The service reads the DEFAULT cache (it does" >&2
    echo "  not inherit KRB5CCNAME from your shell), so every database connection" >&2
    echo "  would fail with 'No Kerberos credentials available' -- while this" >&2
    echo "  script reported success. Refusing to write a unit that cannot work." >&2
    if [[ -n "${KRB5CCNAME:-}" ]]; then
      echo "" >&2
      echo "  Your shell has a usable ticket in a different cache. You almost" >&2
      echo "  certainly want:" >&2
      echo "    --krb5-ccname \"$KRB5CCNAME\"" >&2
    else
      echo "  If a renewal job maintains a non-default cache, pass its path:" >&2
      echo "    --krb5-ccname /tmp/krb5cc_<uid>_auto" >&2
    fi
    echo "" >&2
    echo "  To install the unit anyway (ticket arriving later): --no-krb5-check" >&2
    exit 2
  fi
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
[[ -n "$krb5_ccname" ]] && environment_lines="$environment_lines
Environment=KRB5CCNAME=$krb5_ccname"
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
  systemctl --user enable --now memory-mcp
  systemctl --user status memory-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now memory-mcp"
fi
