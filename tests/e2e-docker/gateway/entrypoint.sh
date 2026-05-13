#!/bin/sh
# captive-gateway entrypoint — starts the three subsystems and waits for any
# of them to exit (then tears the rest down).
#
# - mockportal on 127.0.0.1:18080 (loopback-only; nginx proxies it)
# - nginx on :80 (default_server; intercepts every Host)
# - dnsmasq on :53 (wildcard A → gateway IP) + DHCP on the captive subnet
#
# PORTAL_COMPLETE_AFTER controls how many probes return 302 → /portal before
# the next one returns 204 ("validated"). Defaults to 1 in test mode so the
# post-login probe immediately validates; the unit-test default of 3 is for
# Android/desktop integration suites that probe more aggressively.

set -eu

: "${PORTAL_COMPLETE_AFTER:=1}"

cleanup() {
    [ -n "${MOCKPORTAL_PID:-}" ] && kill "$MOCKPORTAL_PID" 2>/dev/null || true
    [ -n "${DNSMASQ_PID:-}"    ] && kill "$DNSMASQ_PID"    2>/dev/null || true
    [ -n "${NGINX_PID:-}"      ] && kill "$NGINX_PID"      2>/dev/null || true
}
trap cleanup INT TERM

cd /opt/mockportal
PORTAL_COMPLETE_AFTER="$PORTAL_COMPLETE_AFTER" \
    python3 -u -m mockportal.server &
MOCKPORTAL_PID=$!

dnsmasq --no-daemon --conf-file=/etc/dnsmasq.conf &
DNSMASQ_PID=$!

nginx -c /etc/nginx/nginx.conf &
NGINX_PID=$!

echo "captive-gateway up: mockportal=$MOCKPORTAL_PID dnsmasq=$DNSMASQ_PID nginx=$NGINX_PID" >&2

# Wait for any subsystem to exit; surface its exit code.
wait -n
status=$?
cleanup
wait || true
exit "$status"
