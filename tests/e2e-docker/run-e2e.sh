#!/bin/bash
# Top-level E2E orchestrator.
#
# Stages:
#   1. clean previous artifacts
#   2. podman-compose build (gateway + client images)
#   3. podman-compose up — client runs the scenario, exits with its rc
#   4. snapshot the gateway's request log over the published port
#   5. teardown the stack
#   6. run driver/assertions.py over the collected artifacts
#
# Exit code: 0 iff scenario succeeded AND every host-side assertion passed.
# Anything else surfaces "see ./artifacts/ for evidence".
set -euo pipefail

cd "$(dirname "$0")"
ARTIFACTS_DIR="$PWD/artifacts"

log() { printf '[run-e2e] %s\n' "$*" >&2; }

cleanup() {
    log "tearing down compose stack"
    podman-compose down --volumes 2>/dev/null || true
}
trap cleanup EXIT

log "preparing artifacts directory"
mkdir -p "$ARTIFACTS_DIR"
# Stale files would confuse the assertions step — start fresh each run.
find "$ARTIFACTS_DIR" -mindepth 1 -delete

log "building images"
podman-compose build

log "bringing up stack; client will run the scenario and exit"
# --abort-on-container-exit + --exit-code-from forwards the client's exit
# code. With the client's `command: ["test"]` that's whatever
# run-scenario.py returned (0 success, 1 any failure).
set +e
podman-compose up --abort-on-container-exit --exit-code-from gatepath-client
client_rc=$?
set -e
log "client exited with rc=$client_rc"

log "gateway /log was snapshotted by the scenario itself (gateway-log.json in artifacts)"

log "running assertions"
set +e
python3 driver/assertions.py "$ARTIFACTS_DIR"
assertions_rc=$?
set -e

log "summary: client_rc=$client_rc assertions_rc=$assertions_rc"

if [ "$client_rc" -ne 0 ] || [ "$assertions_rc" -ne 0 ]; then
    log "FAIL — see $ARTIFACTS_DIR for evidence"
    exit 1
fi

log "PASS"
exit 0
