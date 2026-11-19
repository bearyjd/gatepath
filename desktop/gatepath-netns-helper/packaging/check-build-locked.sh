#!/usr/bin/env bash
# check-build-locked.sh — assert every packaging script that builds the
# root-privileged helper passes --locked to `cargo build`, so the committed
# Cargo.lock is honoured (the RPM spec does the same).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fail=0
for f in build-sysext.sh gatepath-netns-helper.spec; do
  line="$(grep -E '^[[:space:]]*cargo build ' "$here/$f" || true)"
  if [ -z "$line" ]; then
    echo "FAIL $f: no 'cargo build' line found" >&2; fail=1
  elif ! grep -q -- '--locked' <<<"$line"; then
    echo "FAIL $f: cargo build lacks --locked: $line" >&2; fail=1
  fi
done
[ "$fail" -eq 0 ] && echo "ok: cargo build --locked present"
exit "$fail"
