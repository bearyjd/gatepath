#!/usr/bin/env bash
# Guard: the Gradle wrapper must be checksum-pinned and validated before the
# release job (which holds signing secrets) runs ./gradlew.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
props="$root/android/gradle/wrapper/gradle-wrapper.properties"
wf="$root/.github/workflows/release.yml"
fail=0

grep -Eq '^distributionSha256Sum=[0-9a-f]{64}$' "$props" \
  || { echo "FAIL: $props lacks a 64-hex distributionSha256Sum"; fail=1; }

val_line="$(grep -nE 'uses: gradle/actions/wrapper-validation@[0-9a-f]{40}\b' "$wf" | head -1 | cut -d: -f1 || true)"
build_line="$(grep -nE '\./gradlew' "$wf" | head -1 | cut -d: -f1 || true)"
if [ -z "$val_line" ]; then
  echo "FAIL: $wf has no SHA-pinned gradle/actions/wrapper-validation step"; fail=1
elif [ -n "$build_line" ] && [ "$val_line" -ge "$build_line" ]; then
  echo "FAIL: wrapper-validation (line $val_line) must precede first ./gradlew (line $build_line)"; fail=1
fi
exit "$fail"
