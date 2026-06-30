#!/usr/bin/env bash
#
# validate-sysext.sh — structural check of a built gatepath sysext `.raw`.
# Asserts every expected /usr file is present with the right mode AND is
# root-owned with no group/other write bit, plus the extension-release metadata.
# This does NOT perform a real `systemd-sysext merge` (that needs a privileged
# host) — an on-host merge + helper-start smoke test is a follow-up for the
# self-hosted runner; see DESKTOP_NETNS_DEPLOYMENT.md.
#
# Usage: validate-sysext.sh <image.raw>
# Requires: unsquashfs (squashfs-tools).
set -euo pipefail

IMAGE="${1:?usage: validate-sysext.sh <image.raw>}"
[ -f "$IMAGE" ] || { echo "error: image not found: $IMAGE" >&2; exit 1; }
command -v unsquashfs >/dev/null 2>&1 || {
  echo "error: unsquashfs not found — install squashfs-tools" >&2
  exit 1
}

# path → expected symbolic mode (unsquashfs -ll first column). Executables
# (binary + runner) must be 0755; everything else 0644. install(1) sets these,
# so a mismatch means a regression.
declare -A EXPECT_MODE=(
  ["usr/libexec/gatepath-netns-helper"]="-rwxr-xr-x"
  ["usr/lib/gatepath/portal-webview-runner"]="-rwxr-xr-x"
  ["usr/lib/systemd/system/gatepath-netns-helper.service"]="-rw-r--r--"
  ["usr/lib/tmpfiles.d/gatepath.conf"]="-rw-r--r--"
  ["usr/lib/extension-release.d/extension-release.gatepath-netns-helper"]="-rw-r--r--"
  ["usr/share/factory/etc/logrotate.d/gatepath-netns-helper"]="-rw-r--r--"
  ["usr/share/dbus-1/system.d/cc.grepon.Gatepath.NetNsHelper.conf"]="-rw-r--r--"
  ["usr/share/dbus-1/system-services/cc.grepon.Gatepath.NetNsHelper.service"]="-rw-r--r--"
  ["usr/share/polkit-1/actions/cc.grepon.Gatepath.NetNsHelper.policy"]="-rw-r--r--"
)

listing="$(unsquashfs -ll "$IMAGE")"
fail=0

for path in "${!EXPECT_MODE[@]}"; do
  want="${EXPECT_MODE[$path]}"
  # Exact match on the final field (the path), avoiding regex meta in dotted names.
  line="$(printf '%s\n' "$listing" | awk -v p="squashfs-root/$path" '$NF==p {print; exit}')"
  if [ -z "$line" ]; then
    echo "  MISS $path" >&2; fail=1; continue
  fi
  mode="$(printf '%s' "$line" | awk '{print $1}')"
  owner="$(printf '%s' "$line" | awk '{print $2}')"
  if [ "$mode" != "$want" ]; then
    echo "  FAIL $path mode=$mode (want $want)" >&2; fail=1
  elif [ "$owner" != "root/root" ] && [ "$owner" != "0/0" ]; then
    # -ll prints names where a passwd db exists, numeric (0/0) where it doesn't.
    echo "  FAIL $path owner=$owner (want root-owned)" >&2; fail=1
  else
    echo "  OK   $path  $mode $owner"
  fi
done

# extension-release must declare ID=_any and an ARCHITECTURE.
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
unsquashfs -q -n -d "$tmp/x" "$IMAGE" usr/lib/extension-release.d >/dev/null
er="$tmp/x/usr/lib/extension-release.d/extension-release.gatepath-netns-helper"
grep -qx 'ID=_any' "$er" || { echo "  FAIL extension-release missing 'ID=_any'" >&2; fail=1; }
grep -qE '^ARCHITECTURE=.+' "$er" || { echo "  FAIL extension-release missing ARCHITECTURE" >&2; fail=1; }

if [ "$fail" -eq 0 ]; then
  echo "sysext layout OK"
else
  echo "sysext validation FAILED" >&2
  exit 1
fi
