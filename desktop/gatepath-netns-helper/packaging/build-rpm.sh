#!/usr/bin/env bash
#
# build-rpm.sh — build the gatepath-netns-helper RPM from the crate, the
# conventional/signable alternative to the sysext (docs/DESKTOP_NETNS_DEPLOYMENT.md
# "Option A"). Companion to build-sysext.sh; both are driven by CI (desktop.yml)
# so the two packaging paths can't drift out of sync with the crate's file layout.
#
# Requires: cargo, and rpmbuild (Fedora/RHEL: `dnf install rpm-build rpmdevtools`;
# Debian/Ubuntu CI: `apt-get install rpm`). Staging assembles the crate + the
# repo-root LICENSE/README/deployment-doc (this is a monorepo — those live above
# the crate) into a source tarball whose top dir is the crate.
#
# Output: RPM + SRPM under $TOPDIR/{RPMS,SRPMS} ($TOPDIR defaults to ~/rpmbuild;
# override with $TOPDIR for a throwaway/CI tree).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRATE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$CRATE_DIR/../.." && pwd)"
SPEC="$SCRIPT_DIR/gatepath-netns-helper.spec"

command -v rpmbuild >/dev/null 2>&1 || {
  echo "error: rpmbuild not found — install rpm-build (Fedora) / rpm (Debian)" >&2
  exit 1
}
command -v cargo >/dev/null 2>&1 || { echo "error: cargo not found" >&2; exit 1; }

NAME="gatepath-netns-helper"
VERSION="$(grep -m1 '^version[[:space:]]*=' "$CRATE_DIR/Cargo.toml" | cut -d'"' -f2)"
[ -n "$VERSION" ] || { echo "error: could not read version from Cargo.toml" >&2; exit 1; }

TOPDIR="${TOPDIR:-$HOME/rpmbuild}"
mkdir -p "$TOPDIR"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS}

echo "==> staging source tarball ${NAME}-${VERSION}.tar.gz"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
SRC="$STAGE/${NAME}-${VERSION}"
mkdir -p "$SRC"
# The crate, minus build artifacts / scratch dirs.
tar -C "$CRATE_DIR" \
  --exclude=./target --exclude=./dist --exclude=./fuzz/target \
  --exclude=./.omc --exclude=./.ruff_cache \
  -cf - . | tar -C "$SRC" -xf -
# Repo-root files the spec's %license/%doc reference, staged flat.
cp "$REPO_ROOT/LICENSE" "$REPO_ROOT/README.md" \
   "$REPO_ROOT/docs/DESKTOP_NETNS_DEPLOYMENT.md" "$SRC/"
tar czf "$TOPDIR/SOURCES/${NAME}-${VERSION}.tar.gz" -C "$STAGE" "${NAME}-${VERSION}"

echo "==> rpmbuild -ba"
rpmbuild --define "_topdir $TOPDIR" -ba "$SPEC"

echo "==> built:"
find "$TOPDIR/RPMS" "$TOPDIR/SRPMS" -name "${NAME}-${VERSION}-*.rpm" -print
