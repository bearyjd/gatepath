#!/bin/sh
# Bind mockportal on 0.0.0.0:18080 with PORTAL_COMPLETE_AFTER tuned high.
#
# Why complete_after=1000 (default) and not 1: Android's NetworkMonitor fires
# multiple probes per evaluation (HTTP, HTTPS, fallback). complete_after=1
# burns out after the first probe; subsequent probes return 204 and the
# system marks the network VALIDATED before captive can be detected.
# Empirically observed during PR #34 dogfooding — keep the counter generous.
#
# DOES NOT modify mockportal/server.py. The package's loopback default
# (PORTAL_HOST="127.0.0.1") is a deliberate safeguard against exposing /log
# on the LAN; this launcher rebinds via build_server(host=...) instead.
set -eu

: "${PORTAL_COMPLETE_AFTER:=1000}"
# The QEMU host alias the emulator uses to reach the docker host running this
# container. The server binds 0.0.0.0 (so the container accepts the
# emulator's connection), but must NOT advertise 0.0.0.0 back in its Location
# header — the emulator can't connect to that. The monitor path follows the
# mock's own redirect (unlike the old debug-intent path, which supplied the
# portal URL directly), so this advertised host is load-bearing, not cosmetic.
: "${PORTAL_ADVERTISED_HOST:=10.0.2.2}"

exec python3 -c "
import sys
sys.path.insert(0, '/app')
from mockportal.server import build_server
server, _ = build_server(
    host='0.0.0.0',
    port=18080,
    complete_after=$PORTAL_COMPLETE_AFTER,
    advertised_host='$PORTAL_ADVERTISED_HOST',
)
print('mockportal listening on 0.0.0.0:18080 (complete_after=$PORTAL_COMPLETE_AFTER, advertised_host=$PORTAL_ADVERTISED_HOST)', flush=True)
server.serve_forever()
"
