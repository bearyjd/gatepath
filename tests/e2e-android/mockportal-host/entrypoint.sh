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

exec python3 -c "
import sys
sys.path.insert(0, '/app')
from mockportal.server import build_server
server, _ = build_server(host='0.0.0.0', port=18080, complete_after=$PORTAL_COMPLETE_AFTER)
print('mockportal listening on 0.0.0.0:18080 (complete_after=$PORTAL_COMPLETE_AFTER)', flush=True)
server.serve_forever()
"
