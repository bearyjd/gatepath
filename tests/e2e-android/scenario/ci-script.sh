#!/bin/bash
# CI wrapper: invoked by reactivecircus/android-emulator-runner's `script:`
# input, which only accepts a single shell command. Resolve paths relative
# to the repo root so the action's working dir doesn't matter.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"

cd "$REPO_ROOT"

python3 tests/e2e-android/scenario/run-scenario.py \
    --apk-path android/app/build/outputs/apk/debug/app-debug.apk \
    --emulator-addr emulator-5554 \
    --mockportal-host-url http://10.0.2.2:18080 \
    --mockportal-from-host-url http://localhost:18080 \
    --artifacts-dir tests/e2e-android/artifacts \
    --mode host-post
