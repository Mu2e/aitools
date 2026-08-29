#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'USAGE'
Usage:
  runs-mcp-install-unit.sh [--port <port>] [--host <host>]
                            [--muse-release <release>]
                            [--mikey-keys-file <path>]
                            [--timeout-seconds <secs>] [--blob-timeout-seconds <secs>]
                            [--no-enable]

Renders a systemd --user unit for runs-mcp into THIS install's own share/
directory (share/runs-mcp/runs-mcp.service) -- not into ~/.config -- and
registers it with `systemctl --user link`, which creates a symlink in
~/.config/systemd/user pointing back here. So the real unit file content
stays with the installed code; ~/.config only ever holds a pointer to it.

Nothing here is a credential (runTool needs no secrets -- see
install.sh), so unlike ecl-mcp's install-unit script, config goes straight
into the unit as plain Environment=/ExecStart values, not an
EnvironmentFile=.

--muse-release sets RUNS_MCP_MUSE_RELEASE, consumed by runs-mcp.sh's
`muse setup` call. Defaults to "head" (a continuously-updated CI build --
see runs-mcp.sh's own comment) if not given. Update this once the runTool
work this server depends on is tagged and published as a real release.

--mikey-keys-file sets MIKEY_KEYS_FILE (the path itself isn't sensitive,
only the file's contents are -- mikey's own file permissions are the
actual protection). Omit to leave auth disabled.

ExecStart itself is resolved from this script's own location (same trick
runs-mcp.sh uses to find its sibling), so no path needs to be hand-edited
or substituted.

Safe to re-run (e.g. after changing --port, or after a redeploy):
re-linking and re-enabling an already-installed unit is a no-op other than
picking up the new ExecStart/Environment lines.

Examples:
  <venv>/bin/runs-mcp-install-unit.sh
  <venv>/bin/runs-mcp-install-unit.sh --port 8006 --mikey-keys-file /path/to/keys.json
USAGE
  exit 2
}

port=8006
host=0.0.0.0
muse_release=""
mikey_keys_file=""
timeout_seconds=""
blob_timeout_seconds=""
do_enable=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --host) host="$2"; shift 2 ;;
    --muse-release) muse_release="$2"; shift 2 ;;
    --mikey-keys-file) mikey_keys_file="$2"; shift 2 ;;
    --timeout-seconds) timeout_seconds="$2"; shift 2 ;;
    --blob-timeout-seconds) blob_timeout_seconds="$2"; shift 2 ;;
    --no-enable) do_enable=0; shift ;;
    --help|-h) usage ;;
    *) echo "ERROR: unknown argument: $1" >&2; usage ;;
  esac
done

script_dir="$(cd "$(dirname "$0")" && pwd)"
venv_root="$(cd "$script_dir/.." && pwd)"
share_dir="$venv_root/share/runs-mcp"
unit_file="$share_dir/runs-mcp.service"

mkdir -p "$share_dir"

exec_start="$script_dir/runs-mcp.sh --host=$host --port=$port"
[[ -n "$timeout_seconds" ]] && exec_start="$exec_start --timeout-seconds=$timeout_seconds"
[[ -n "$blob_timeout_seconds" ]] && exec_start="$exec_start --blob-timeout-seconds=$blob_timeout_seconds"

environment_lines="Environment=RUNS_MCP_MUSE_RELEASE=${muse_release:-head}"
[[ -n "$mikey_keys_file" ]] && environment_lines="$environment_lines
Environment=MIKEY_KEYS_FILE=$mikey_keys_file"

cat > "$unit_file" <<EOF
[Unit]
Description=runs-mcp (read-only streamable-HTTP MCP server for the Mu2e run database)
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
  systemctl --user enable --now runs-mcp
  systemctl --user status runs-mcp --no-pager
else
  echo "Run manually:"
  echo "  systemctl --user enable --now runs-mcp"
fi
