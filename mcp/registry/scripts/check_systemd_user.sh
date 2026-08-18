#!/usr/bin/env bash
# check_systemd_user.sh
#
# Non-destructive diagnostic: can this account run a persistent
# `systemctl --user` service here (needed to keep registry-mcp, and later
# the other HTTP MCPs, running independent of any login session)?
#
# Run this in the SERVER account, on the host where registry-mcp will be
# deployed. Safe to run repeatedly; cleans up everything it creates.

set -uo pipefail

pass=0
fail=0
warn=0

ok()   { printf '[OK]   %s\n' "$1"; pass=$((pass+1)); }
bad()  { printf '[FAIL] %s\n' "$1"; fail=$((fail+1)); }
note() { printf '[WARN] %s\n' "$1"; warn=$((warn+1)); }

echo "== systemd --user access check =="
echo "User: $(id -un)   Host: $(hostname -f 2>/dev/null || hostname)"
echo

# 1. XDG_RUNTIME_DIR present (user manager needs this)
if [[ -n "${XDG_RUNTIME_DIR:-}" && -d "${XDG_RUNTIME_DIR}" ]]; then
  ok "XDG_RUNTIME_DIR is set: $XDG_RUNTIME_DIR"
else
  bad "XDG_RUNTIME_DIR is not set or missing. A user systemd instance is likely not available in this session."
fi

# 2. user manager reachable
if systemctl --user show-environment >/dev/null 2>&1; then
  ok "systemctl --user is reachable"
else
  bad "systemctl --user is not reachable (no user manager running for this session)"
fi

# 3. linger (keeps user services running after you log out / close the ssh session)
if command -v loginctl >/dev/null 2>&1; then
  linger_state=$(loginctl show-user "$(id -un)" -p Linger 2>/dev/null || true)
  if [[ "$linger_state" == "Linger=yes" ]]; then
    ok "Linger is enabled ($linger_state) -- user services survive logout"
  else
    note "Linger is not enabled (${linger_state:-unknown}). The service will stop when your last session ends unless linger is turned on: loginctl enable-linger $(id -un) (may require admin/root)"
  fi
else
  note "loginctl not found; cannot check linger state"
fi

# 4. can we write unit files
unit_dir="$HOME/.config/systemd/user"
mkdir -p "$unit_dir" 2>/dev/null
if [[ -w "$unit_dir" ]]; then
  ok "Can write unit files to $unit_dir"
else
  bad "Cannot write to $unit_dir"
fi

# 5. end-to-end smoke test: create, start, verify, and remove a harmless transient unit
echo
echo "-- transient unit smoke test --"
test_unit="registry-mcp-selftest-$$"
test_log="/tmp/${test_unit}.log"
if systemctl --user show-environment >/dev/null 2>&1; then
  if systemd-run --user --unit="$test_unit" --collect \
       -p RemainAfterExit=no /bin/sleep 5 >"$test_log" 2>&1; then
    sleep 1
    if systemctl --user is-active "$test_unit" >/dev/null 2>&1; then
      ok "Transient unit started and is active: $test_unit"
    else
      note "Transient unit created but not reported active (it may have already finished)"
    fi
    systemctl --user stop "$test_unit" >/dev/null 2>&1 || true
    systemctl --user reset-failed "$test_unit" >/dev/null 2>&1 || true
    ok "Transient unit stopped and cleaned up"
  else
    bad "systemd-run --user failed: $(cat "$test_log" 2>/dev/null)"
  fi
else
  note "Skipping transient unit test (no user manager)"
fi
rm -f "$test_log"

# 6. port bind check (0.0.0.0:8000, registry-mcp's assigned port)
echo
echo "-- port bind check (0.0.0.0:8000) --"
if command -v python3 >/dev/null 2>&1; then
  if python3 - <<'PY'
import socket, sys
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", 8000))
    s.listen(1)
    print("[OK]   Bound 0.0.0.0:8000 successfully (released immediately)")
except OSError as e:
    print(f"[FAIL] Could not bind 0.0.0.0:8000: {e}")
    sys.exit(1)
finally:
    s.close()
PY
  then
    pass=$((pass+1))
  else
    fail=$((fail+1))
  fi
else
  note "python3 not found; skipped port bind check"
fi

echo
echo "== Summary: $pass ok, $warn warning(s), $fail failed =="
if [[ $fail -gt 0 ]]; then
  echo "systemd --user deployment is likely NOT usable as-is in this account/session."
  exit 1
elif [[ $warn -gt 0 ]]; then
  echo "systemd --user works, but check the warning(s) above (e.g. linger) before relying on it long-term."
  exit 0
else
  echo "systemd --user looks fully usable for a persistent registry-mcp service."
  exit 0
fi
