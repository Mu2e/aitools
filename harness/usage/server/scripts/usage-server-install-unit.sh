#!/usr/bin/env bash
# Render the systemd --user unit of the usage log service into this
# install's share/ directory and register it (systemctl --user link), as the
# MCP servers' install-unit scripts do.
#
#   usage-server-install-unit.sh --mikey-keys-file PATH --krb5-ccname CCNAME
#                                [--dsn DSN] [--port PORT] [--host HOST] [--no-enable]
#
#   --mikey-keys-file  the hashed mikey key file (required; not a secret path)
#   --krb5-ccname      the Kerberos cache used for the database, e.g.
#                      FILE:/tmp/krb5cc_18199_auto. A systemd --user service
#                      does not inherit KRB5CCNAME from your shell: without
#                      this it reads the default cache, which may be expired
#                      while the renewed one is fine.
#   --dsn              default postgresql://ifdb11:5477/mu2e_ai_prd
#   --port             default 8199
#   --no-enable        write and link the unit, but do not enable or start it
#
# Safe to re-run. It does not restart a running service:
#   systemctl --user restart usage-server
set -euo pipefail

usage() { sed -n '2,/^set /s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" | sed '$d' >&2; exit 2; }

keys="" ccname="" dsn="postgresql://ifdb11:5477/mu2e_ai_prd" port=8199 host=0.0.0.0 enable=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mikey-keys-file) keys="${2:?}"; shift 2 ;;
    --krb5-ccname) ccname="${2:?}"; shift 2 ;;
    --dsn) dsn="${2:?}"; shift 2 ;;
    --port) port="${2:?}"; shift 2 ;;
    --host) host="${2:?}"; shift 2 ;;
    --no-enable) enable=0; shift ;;
    *) usage ;;
  esac
done
[[ -n "$keys" ]] || { echo "ERROR: --mikey-keys-file is required" >&2; usage; }
[[ -r "$keys" ]] || { echo "ERROR: cannot read $keys" >&2; exit 2; }
if [[ -n "$ccname" ]]; then
  KRB5CCNAME="$ccname" klist -s 2>/dev/null \
    || { echo "ERROR: no valid Kerberos ticket in $ccname (klist -s)" >&2; exit 2; }
else
  echo "NOTE: no --krb5-ccname: the service will use this account's default Kerberos cache." >&2
fi

bin_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
venv_dir="$(dirname "$bin_dir")"
# <deploy-root>/releases/<ref>/.venv, where <ref> may contain a slash (a
# branch like sc/usage-service): the deploy root is what comes before
# /releases/, not a fixed number of levels up.
deploy_root="${venv_dir%%/releases/*}"
# Through current, so a new release is picked up by a restart alone.
exec_start="$deploy_root/current/.venv/bin/usage-server"
[[ -x "$exec_start" ]] || exec_start="$bin_dir/usage-server"

share="$venv_dir/share/usage-server"
mkdir -p "$share"
unit="$share/usage-server.service"
{
  echo "[Unit]"
  echo "Description=Mu2e AI usage log service (takes the harnesses' usage records)"
  echo "After=network-online.target"
  echo "Wants=network-online.target"
  echo
  echo "[Service]"
  echo "Type=simple"
  echo "Environment=MIKEY_KEYS_FILE=$keys"
  echo "Environment=LOG_PG_DSN=$dsn"
  [[ -n "$ccname" ]] && echo "Environment=KRB5CCNAME=$ccname"
  echo "ExecStart=$exec_start --host=$host --port=$port"
  echo "Restart=on-failure"
  echo "RestartSec=5"
  echo
  echo "[Install]"
  echo "WantedBy=default.target"
} > "$unit"
echo "Wrote $unit"
systemctl --user link "$unit"
systemctl --user daemon-reload
if [[ "$enable" == 1 ]]; then
  systemctl --user enable --now usage-server
  echo "Enabled and started: systemctl --user status usage-server"
else
  echo "Linked. Start it with: systemctl --user enable --now usage-server"
fi
echo "Check: curl -s http://localhost:$port/health"
