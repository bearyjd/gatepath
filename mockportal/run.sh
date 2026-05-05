#!/usr/bin/env bash
# Start the Gatepath mock captive portal server.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/server.py"
