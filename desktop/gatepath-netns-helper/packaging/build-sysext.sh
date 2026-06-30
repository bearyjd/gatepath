#!/usr/bin/env bash
#
# build-sysext.sh — assemble gatepath-netns-helper into a systemd-sysext image
# (a squashfs `.raw`). Every helper file installs under /usr, so the sysext
# overlays a read-only /usr (Bazzite / Fedora Atomic / any immutable distro)
# with NO source edits — PORTAL_RUNNER_PATH already defaults to
# /usr/lib/gatepath/portal-webview-runner.
#
# Output: <crate>/dist/gatepath-netns-helper.raw   (override dir with $OUT_DIR)
#
# Requires: cargo, and mksquashfs (squashfs-tools). Install squashfs-tools via
# `apt-get install squashfs-tools` / `dnf install squashfs-tools`. CI installs
# it — see .github/workflows/desktop.yml (build-sysext job).
#
# Layout produced (all root-owned via mksquashfs -all-root):
#   usr/libexec/gatepath-netns-helper                         (release binary)
#   usr/lib/gatepath/portal-webview-runner
#   usr/lib/systemd/system/gatepath-netns-helper.service
#   usr/lib/tmpfiles.d/gatepath.conf
#   usr/lib/extension-release.d/extension-release.gatepath-netns-helper
#   usr/share/dbus-1/system.d/cc.grepon.Gatepath.NetNsHelper.conf
#   usr/share/dbus-1/system-services/cc.grepon.Gatepath.NetNsHelper.service
#   usr/share/polkit-1/actions/cc.grepon.Gatepath.NetNsHelper.policy
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRATE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$CRATE_DIR/data"

# systemd ARCHITECTURE= identifier. Must match what `cargo build` actually
# produces; override for a cross target (this script does not cross-compile).
ARCH="${ARCH:-x86-64}"
OUT_DIR="${OUT_DIR:-$CRATE_DIR/dist}"
IMAGE_NAME="gatepath-netns-helper"      # MUST match extension-release.<name>
IMAGE="$OUT_DIR/${IMAGE_NAME}.raw"
VERSION="$(grep -m1 '^version[[:space:]]*=' "$CRATE_DIR/Cargo.toml" | cut -d'"' -f2)"
[ -n "$VERSION" ] || VERSION="0"        # workspace-inherited version → fallback

command -v mksquashfs >/dev/null 2>&1 || {
  echo "error: mksquashfs not found — install squashfs-tools" >&2
  exit 1
}

echo "==> building helper (release)"
cargo build --release --manifest-path "$CRATE_DIR/Cargo.toml"
BIN="$CRATE_DIR/target/release/gatepath-netns-helper"
[ -x "$BIN" ] || { echo "error: helper binary not found at $BIN" >&2; exit 1; }

STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT

echo "==> staging /usr tree"
install -Dm0755 "$BIN" \
  "$STAGING/usr/libexec/gatepath-netns-helper"
install -Dm0755 "$DATA_DIR/portal-webview-runner" \
  "$STAGING/usr/lib/gatepath/portal-webview-runner"
install -Dm0644 "$DATA_DIR/gatepath-netns-helper.service" \
  "$STAGING/usr/lib/systemd/system/gatepath-netns-helper.service"
install -Dm0644 "$DATA_DIR/cc.grepon.Gatepath.NetNsHelper.conf" \
  "$STAGING/usr/share/dbus-1/system.d/cc.grepon.Gatepath.NetNsHelper.conf"
install -Dm0644 "$DATA_DIR/cc.grepon.Gatepath.NetNsHelper.service" \
  "$STAGING/usr/share/dbus-1/system-services/cc.grepon.Gatepath.NetNsHelper.service"
install -Dm0644 "$DATA_DIR/cc.grepon.Gatepath.NetNsHelper.policy" \
  "$STAGING/usr/share/polkit-1/actions/cc.grepon.Gatepath.NetNsHelper.policy"
install -Dm0644 "$SCRIPT_DIR/tmpfiles.d/gatepath.conf" \
  "$STAGING/usr/lib/tmpfiles.d/gatepath.conf"
# Audit-log rotation policy. A sysext overlays only /usr, never /etc — so this
# /etc config is carried under /usr/share/factory/ and copied to /etc by the
# tmpfiles `C` line in gatepath.conf on `systemd-tmpfiles --create`. Without it,
# a caller spamming SetupCaptive could grow the audit log unboundedly.
install -Dm0644 "$DATA_DIR/gatepath-helper-audit.logrotate" \
  "$STAGING/usr/share/factory/etc/logrotate.d/gatepath-netns-helper"

echo "==> writing sysext metadata (ID=_any, ARCHITECTURE=$ARCH, version $VERSION)"
install -d "$STAGING/usr/lib/extension-release.d"
cat > "$STAGING/usr/lib/extension-release.d/extension-release.${IMAGE_NAME}" <<EOF
# gatepath-netns-helper systemd-sysext — version ${VERSION}
# ID=_any: merges on any distribution (OS version match skipped). sysext(8).
ID=_any
ARCHITECTURE=${ARCH}
EOF

echo "==> building squashfs image"
mkdir -p "$OUT_DIR"
rm -f "$IMAGE"
# -all-root: force uid/gid 0 (build runs unprivileged; merged files must be
# root-owned). -noappend: fresh image. -no-xattrs: portable + deterministic.
mksquashfs "$STAGING" "$IMAGE" -all-root -noappend -no-xattrs -quiet

echo "==> built: $IMAGE ($(du -h "$IMAGE" | cut -f1))"
