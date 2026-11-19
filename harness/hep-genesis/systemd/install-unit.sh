#!/usr/bin/env bash
# Register the systemd --user unit that runs hep-genesis automations for a
# service account (mu2eai): one service, one process, every automation of the
# data folder (each <project>/watches/<name>.json; "enabled": false leaves
# one out).
#
#   <install>/current/systemd/install-unit.sh --data-dir <path> --env-file <path> [options]
#
#   systemctl --user enable --now hep-genesis-automations
#   systemctl --user restart      hep-genesis-automations   # a new or re-enabled automation, an upgrade
#   journalctl --user -u          hep-genesis-automations -f -o cat
#
# An edited automation needs no restart: it loads its file again within
# seconds. One switched off ("enabled": false) ends the same way.
#
# The unit is rendered into this release (share/) and registered with
# `systemctl --user link`, so ~/.config/systemd holds only a symlink. Its
# command points at <deploy-root>/current: a new release takes effect on a
# restart. Re-run this script after an install whenever the unit template or
# these options change, and before the release the link points into is pruned.
#
#   --data-dir <path>   REQUIRED, absolute. The service's data folder
#                       (projects/, workspace/, servers.json, models.json);
#                       under /exp/mu2e/data, not beside the code.
#   --env-file <path>   REQUIRED. Its .env: model endpoint and keys, the
#                       service's Mikey key (MIKEY_TOKEN). Mode 600.
#   --home <path>       HOME for the service (default: <data-dir>/home). Model
#                       logins and caches go there, not to a NAS home that an
#                       unattended service should not depend on.
#   --deploy-root <p>   Folder holding releases/ and current (default: found
#                       from this script's place).
#   --dry-run           Print the unit and exit.
#
# Once per account, or the services stop at logout:  loginctl enable-linger
#
# With a web UI on the same data folder: Stop there ends that automation
# until the service is restarted, and Start runs it from the web server, not
# from this service. To switch one off for good, untick Enabled in its form.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
usage() { sed -n '2,/^$/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}" >&2; exit 2; }

data="" env_file="" home="" root="" dry=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) data="${2:?}"; shift 2 ;;
    --env-file) env_file="${2:?}"; shift 2 ;;
    --home) home="${2:?}"; shift 2 ;;
    --deploy-root) root="${2:?}"; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *) usage ;;
  esac
done
[[ -n "$data" && -n "$env_file" ]] || usage
[[ "$data" == /* && "$env_file" == /* ]] || { echo "ERROR: --data-dir and --env-file must be absolute" >&2; exit 2; }
home="${home:-$data/home}"
# <deploy-root>/releases/<commit>/systemd/install-unit.sh
root="${root:-$(cd "$here/../../.." && pwd -P)}"
[[ -x "$root/current/bin/hep-genesis-agent" ]] || { echo "ERROR: no install at $root/current (pass --deploy-root)" >&2; exit 2; }

unit="$(sed -e "s|@DATA_DIR@|$data|g" -e "s|@HOME_DIR@|$home|g" -e "s|@ENV_FILE@|$env_file|g" \
            -e "s|@DEPLOY_ROOT@|$root|g" "$here/hep-genesis-automations.service.in")"
if [[ "$dry" == 1 ]]; then
  printf '%s\n' "$unit"
  exit 0
fi

[[ -f "$env_file" ]] || { echo "ERROR: $env_file does not exist (copy site/env.template, fill it in, chmod 600)" >&2; exit 2; }
mode="$(stat -c %a "$env_file")"
[[ "$mode" == "600" || "$mode" == "400" ]] || { echo "ERROR: $env_file is mode $mode; it holds keys: chmod 600" >&2; exit 2; }
mkdir -p "$data" "$home" "$data/projects" "$data/workspace" "$data/staging" "$data/artifacts"
chmod 700 "$data"
[[ -f "$data/servers.json" ]] || { cp "$root/current/site/servers.json" "$data/servers.json"; chmod 600 "$data/servers.json"; echo "Created $data/servers.json"; }

share="$(cd "$here/.." && pwd -P)/share"
mkdir -p "$share"
printf '%s\n' "$unit" > "$share/hep-genesis-automations.service"
# The unit of earlier releases: one instance per automation.
if systemctl --user cat 'hep-genesis-watch@.service' >/dev/null 2>&1; then
  systemctl --user stop 'hep-genesis-watch@*' 2>/dev/null || true
  cfg="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
  rm -f "$cfg/hep-genesis-watch@.service" "$cfg"/*.wants/hep-genesis-watch@*.service
  echo "Removed the earlier unit hep-genesis-watch@.service"
fi
systemctl --user link --force "$share/hep-genesis-automations.service"
systemctl --user daemon-reload

if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  echo "NOTE: linger is off for $USER: the services stop when you log out. Run: loginctl enable-linger"
fi
echo "Unit linked: $share/hep-genesis-automations.service"
echo "Start:  systemctl --user enable --now hep-genesis-automations"
echo "Log:    journalctl --user -u hep-genesis-automations -f -o cat"
