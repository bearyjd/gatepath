#!/bin/bash
# Top-level Android e2e orchestrator (local dev path).
#
# Stages:
#   1. clean previous artifacts
#   2. compose build (mockportal-host image)
#   3. compose up -d (mockportal-host + budtmo emulator)
#   4. wait for emulator boot, run scenario, exit with its rc
#   5. run driver/assertions.py over collected artifacts
#   6. tear down stack on exit (always)
#
# Exit 0 iff scenario_rc == 0 AND assertions_rc == 0.
#
# Tuning knobs:
#   APK_PATH         path to the debug APK (default: android/app/build/outputs/apk/debug/app-debug.apk)
#   SCENARIO_MODE    host-post (default) | ui — see scenario/run-scenario.py
#   COMPOSE          which compose tool (default: 'docker compose')
#
# CI uses .github/workflows/android-e2e.yml instead of this script.

set -euo pipefail

cd "$(dirname "$0")"
ARTIFACTS_DIR="$PWD/artifacts"
REPO_ROOT="$(cd ../.. && pwd)"
APK_PATH="${APK_PATH:-$REPO_ROOT/android/app/build/outputs/apk/debug/app-debug.apk}"
SCENARIO_MODE="${SCENARIO_MODE:-host-post}"
COMPOSE="${COMPOSE:-docker compose}"

log() { printf '[run-e2e] %s\n' "$*" >&2; }

cleanup() {
    log "tearing down compose stack"
    $COMPOSE down --volumes 2>/dev/null || true
}
trap cleanup EXIT

log "preparing artifacts directory"
mkdir -p "$ARTIFACTS_DIR"
find "$ARTIFACTS_DIR" -mindepth 1 ! -name '.gitkeep' -delete

if [ ! -f "$APK_PATH" ]; then
    log "APK not found at $APK_PATH"
    log "build it with:"
    log "  (cd $REPO_ROOT/android && ANDROID_HOME=\"\$ANDROID_HOME\" ./gradlew :app:assembleDebug)"
    exit 1
fi

log "building images (mockportal-host)"
$COMPOSE build

log "starting compose stack (emulator boot can take 60-180s)"
$COMPOSE up -d

log "running scenario (apk=$APK_PATH, mode=$SCENARIO_MODE)"
set +e
python3 scenario/run-scenario.py \
    --apk-path "$APK_PATH" \
    --emulator-addr localhost:5555 \
    --mockportal-host-url http://10.0.2.2:18080 \
    --mockportal-from-host-url http://localhost:18080 \
    --artifacts-dir "$ARTIFACTS_DIR" \
    --mode "$SCENARIO_MODE"
scenario_rc=$?
set -e
log "scenario exited with rc=$scenario_rc"

log "running assertions"
set +e
python3 driver/assertions.py "$ARTIFACTS_DIR"
assertions_rc=$?
set -e

log "summary: scenario_rc=$scenario_rc assertions_rc=$assertions_rc"

if [ "$scenario_rc" -ne 0 ] || [ "$assertions_rc" -ne 0 ]; then
    log "FAIL — see $ARTIFACTS_DIR for evidence"
    exit 1
fi

log "PASS"
exit 0
