#!/usr/bin/env bash
#
# build-rpm.sh — build the gatepath-netns-helper RPM from the crate, the
# conventional/signable alternative to the sysext (docs/DESKTOP_NETNS_DEPLOYMENT.md
# "Option A"). Companion to build-sysext.sh; both are driven by CI (desktop.yml)
# so the two packaging paths can't drift out of sync with the crate's file layout.
#
# Requires: cargo, rpmbuild, and git (Fedora/RHEL: `dnf install rpm-build
# systemd-rpm-macros cargo rust git`; Debian/Ubuntu: `apt-get install rpm`).
#
# The source tarball is built from **committed content** (`git archive HEAD`), so
# it is reproducible and can never bundle untracked/gitignored scratch files (e.g.
# nested .omc/) into the SRPM. The crate is the tarball root; the repo-root
# LICENSE/README/deployment-doc are staged flat into it (this is a monorepo — those
# live above the crate). The RPM Version is read from the committed Cargo.toml and
# passed to rpmbuild, so it can't drift from the spec.
#
# Output: RPM + SRPM under $TOPDIR/{RPMS,SRPMS} ($TOPDIR defaults to ~/rpmbuild;
# override with $TOPDIR for a throwaway/CI tree).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRATE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$CRATE_DIR/../.." && pwd)"
CRATE_REL="${CRATE_DIR#"$REPO_ROOT"/}"
SPEC="$SCRIPT_DIR/gatepath-netns-helper.spec"

for tool in rpmbuild cargo git; do
  command -v "$tool" >/dev/null 2>&1 || { echo "error: $tool not found" >&2; exit 1; }
done
git -C "$REPO_ROOT" rev-parse HEAD >/dev/null 2>&1 || {
  echo "error: $REPO_ROOT is not a git checkout (needed for a reproducible tarball)" >&2
  exit 1
}

NAME="gatepath-netns-helper"
# Read the version from the COMMITTED Cargo.toml so the tarball name, the rpmbuild
# --define, and the archived source all agree.
VERSION="$(git -C "$REPO_ROOT" show "HEAD:$CRATE_REL/Cargo.toml" \
  | grep -m1 '^version[[:space:]]*=' | cut -d'"' -f2)"
[ -n "$VERSION" ] || { echo "error: could not read version from committed Cargo.toml" >&2; exit 1; }

TOPDIR="${TOPDIR:-$HOME/rpmbuild}"
mkdir -p "$TOPDIR"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS}

echo "==> staging source tarball ${NAME}-${VERSION}.tar.gz (git archive HEAD:$CRATE_REL)"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
SRC="$STAGE/${NAME}-${VERSION}"
mkdir -p "$SRC"
# Committed crate files only, crate-root-relative (target/dist/fuzz-target/.omc are
# gitignored, so they're absent from HEAD automatically).
git -C "$REPO_ROOT" archive --format=tar "HEAD:$CRATE_REL" | tar -C "$SRC" -xf -
# Repo-root files the spec's %license/%doc reference, staged flat, also from HEAD.
for f in LICENSE README.md docs/DESKTOP_NETNS_DEPLOYMENT.md; do
  git -C "$REPO_ROOT" show "HEAD:$f" > "$SRC/$(basename "$f")"
done
tar czf "$TOPDIR/SOURCES/${NAME}-${VERSION}.tar.gz" -C "$STAGE" "${NAME}-${VERSION}"

echo "==> rpmbuild -ba (version $VERSION)"
rpmbuild --define "_topdir $TOPDIR" --define "version $VERSION" -ba "$SPEC"

echo "==> built:"
find "$TOPDIR/RPMS" "$TOPDIR/SRPMS" -name "${NAME}-${VERSION}-*.rpm" -print
