#!/usr/bin/env bash
#
# collect-diagnostics.sh — gather a support bundle for the Gatepath desktop
# captive-portal helper (gatepath-netns-helper). Run it on the box where the
# helper is deployed. Use sudo to include the root-owned helper audit log
# (/var/lib/gatepath/helper-audit.jsonl, mode 0640):
#
#   sudo desktop/gatepath-netns-helper/packaging/collect-diagnostics.sh
#   sudo .../collect-diagnostics.sh --redact      # strip SSIDs + gateway IPs
#
# Output: ./gatepath-diagnostics-<host>-<UTC>.tar.gz  (override dir via $OUT_DIR).
# See docs/TROUBLESHOOTING.md for how to read it.
#
# PRIVACY: the bundle includes the helper + user audit logs (D-Bus sender, the
# Wi-Fi interface name, timestamps; the user log adds SSID + gateway IP) and
# NetworkManager / journal output. REVIEW IT BEFORE SHARING. `--redact` strips
# SSIDs, gateway IPs, and portal domains from the copied audit logs; it does NOT
# scrub journald/nmcli output (which can still contain SSIDs).
set -euo pipefail

REDACT=0
[ "${1:-}" = "--redact" ] && REDACT=1

HELPER_AUDIT="/var/lib/gatepath/helper-audit.jsonl"
USER_AUDIT="${XDG_DATA_HOME:-$HOME/.local/share}/gatepath/audit.jsonl"
UNIT="gatepath-netns-helper.service"
JOURNAL_LINES="${JOURNAL_LINES:-2000}"
AUDIT_TAIL="${AUDIT_TAIL:-5000}"
OUT_DIR="${OUT_DIR:-$PWD}"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
host="$(hostname 2>/dev/null || uname -n)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
name="gatepath-diagnostics-${host}-${ts}"
bundle="$work/$name"
mkdir -p "$bundle"

# Run a command, capturing stdout+stderr+exit into the bundle. Never aborts the
# script: a missing tool or inactive unit is itself useful signal, recorded in
# the file rather than fatal.
cap() {
  local out="$bundle/$1"; shift
  printf '## %s\n' "$*" > "$out"
  "$@" >> "$out" 2>&1 || printf '## (command unavailable or failed, exit=%s)\n' "$?" >> "$out"
}

echo "==> environment"
{
  echo "collected_utc=$ts"
  echo "host=$host"
  echo "euid=$(id -u)  (0/root needed to read the helper audit log)"
  uname -a
  ( . /etc/os-release 2>/dev/null && echo "os=$PRETTY_NAME ($ID ${VERSION_ID:-})" ) || true
} > "$bundle/environment.txt" 2>&1

echo "==> service + journal"
cap service-status.txt systemctl status "$UNIT" --no-pager
cap service-show.txt   systemctl show "$UNIT" --no-pager
cap journal.txt        journalctl -u "$UNIT" --no-pager -n "$JOURNAL_LINES"
cap sysext.txt         systemd-sysext status

echo "==> network + netns + dbus"
cap netns-list.txt  ip netns list
cap nm-general.txt  nmcli general status
cap nm-devices.txt  nmcli -f DEVICE,TYPE,STATE,CONNECTION device status
cap dbus-name.txt   busctl status cc.grepon.Gatepath.NetNsHelper

echo "==> tool versions"
cap versions.txt sh -c '
  for b in iw wpa_supplicant dnsmasq nmcli ip python3 udhcpc dhclient; do
    printf "%s: " "$b"
    if command -v "$b" >/dev/null 2>&1; then "$b" --version 2>&1 | head -1; else echo MISSING; fi
  done'

# Audit logs are the substance. Bounded to the tail; redacted on request.
redact() {
  if [ "$REDACT" = 1 ]; then
    sed -E 's/("ssid":")[^"]*/\1REDACTED/g;
            s/("gateway_ip":")[^"]*/\1REDACTED/g;
            s/("portal_domain":")[^"]*/\1REDACTED/g'
  else
    cat
  fi
}

echo "==> audit logs"
if [ -r "$HELPER_AUDIT" ]; then
  tail -n "$AUDIT_TAIL" "$HELPER_AUDIT" | redact > "$bundle/helper-audit.jsonl"
else
  echo "UNREADABLE: $HELPER_AUDIT — re-run with sudo, or the helper has never run." \
    > "$bundle/helper-audit.jsonl.MISSING"
fi
if [ -r "$USER_AUDIT" ]; then
  tail -n "$AUDIT_TAIL" "$USER_AUDIT" | redact > "$bundle/user-audit.jsonl"
else
  echo "NOT FOUND: $USER_AUDIT" > "$bundle/user-audit.jsonl.MISSING"
fi

echo "==> packing"
mkdir -p "$OUT_DIR"
tarball="$OUT_DIR/${name}.tar.gz"
tar -C "$work" -czf "$tarball" "$name"
echo "==> wrote $tarball"
echo "    Review it before sharing (see docs/TROUBLESHOOTING.md → Privacy)."
