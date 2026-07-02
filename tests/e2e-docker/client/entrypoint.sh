#!/bin/bash
# gatepath-client entrypoint.
#
# Orchestrates the per-container fixtures the desktop stack needs that
# would normally come from systemd/logind/NetworkManager:
#
#   1. Rename the default container iface to wlan0 (Python NM lookup +
#      Rust helper both key off the interface name).
#   2. Point /etc/resolv.conf at the captive gateway (DNS hijack target).
#   3. Start a system dbus-daemon.
#   4. Start polkitd against that bus (auto-YES rules already installed).
#   5. Start the dbusmock NetworkManager so the captivity lookups succeed.
#   6. Start Xvfb so the WebKit subprocess can render headlessly.
#   7. Dispatch to the requested mode (test | wait | shell).
#
# Mode is the first argument:
#   test  — run /opt/e2e/run-scenario.py as the tester user; exit on success
#   wait  — sleep forever (useful with `podman exec` for interactive debug)
#   shell — drop to bash for poking around
#
set -euo pipefail

MODE="${1:-test}"
GATEWAY_IP="172.30.0.2"
WLAN_NAME="wlan0"
TESTER_UID=1000

log() { printf '[entrypoint] %s\n' "$*" >&2; }

# ─── 1. Iface rename ─────────────────────────────────────────────────────
# Find the interface on the CAPTIVE subnet (172.30.0.0/24) — that's the one
# that becomes wlan0 and gets moved into the gatepath netns. The client now
# ALSO has an iface on the trusted net (172.31.0.0/24, where the no-leak
# sentinel lives); that one must stay in the host netns, so "first non-lo"
# is no longer correct. Selecting by subnet is authoritative — if no captive
# iface is present that's a setup error we want surfaced, not papered over by
# grabbing the wrong (trusted) iface, so there is deliberately no fallback.
captive_iface() {
    # `ip -4 -o addr show` field 4 is "<addr>/<prefix>"; field 2 is the clean
    # ifname. Print the first iface whose address is on the captive subnet.
    /usr/sbin/ip -4 -o addr show \
        | awk '$4 ~ /^172\.30\.0\./ { sub(/@.*/, "", $2); print $2; exit }'
}

orig_iface="$(captive_iface || true)"
if [ -z "$orig_iface" ]; then
    log "FATAL: no interface on the captive subnet (172.30.0.0/24) found"
    /usr/sbin/ip -4 -o addr show >&2
    exit 1
fi

if [ "$orig_iface" != "$WLAN_NAME" ]; then
    log "renaming $orig_iface → $WLAN_NAME (preserving L3 config)"
    # Snapshot IPv4 addrs, then rename, then restore them on the renamed
    # iface. The kernel preserves addresses across `ip link set … name …`
    # in practice but the documented contract doesn't guarantee it, so we
    # save and `ip addr replace` to be deterministic. Default route is
    # not saved — we always install GATEWAY_IP after the rename (see
    # below for why).
    saved_addrs="$(/usr/sbin/ip -4 -o addr show dev "$orig_iface" \
        | awk '{print $4}')"
    log "  saved addrs=[$saved_addrs]"

    /usr/sbin/ip link set "$orig_iface" down
    /usr/sbin/ip link set "$orig_iface" name "$WLAN_NAME"
    /usr/sbin/ip link set "$WLAN_NAME" up

    # Re-add addresses that the kernel kept on the netdev but that the
    # rename dance may have evicted from the userspace cache. `replace`
    # is idempotent so it works whether the kernel kept them or not.
    for addr in $saved_addrs; do
        /usr/sbin/ip addr replace "$addr" dev "$WLAN_NAME"
    done
    # Force the default route through the captive-gateway container,
    # not whatever podman auto-assigned. With no `gateway:` in
    # compose.yml's IPAM, netavark assigns the bridge an arbitrary IP
    # (.1 typically) and adds it as the container's default. But the
    # captive AP we're simulating is the gateway CONTAINER (.2), not
    # the bridge. Overwrite the route unconditionally.
    /usr/sbin/ip route replace default via "$GATEWAY_IP" dev "$WLAN_NAME"
    log "  post-rename state:"
    /usr/sbin/ip -4 addr show dev "$WLAN_NAME" >&2 || true
    /usr/sbin/ip -4 route show >&2 || true
fi

# ─── 2. resolv.conf → captive gateway ────────────────────────────────────
log "pinning resolv.conf at $GATEWAY_IP"
cat >/etc/resolv.conf <<EOF
nameserver $GATEWAY_IP
options timeout:2 attempts:1
EOF

# Reachability sanity-check. If this fails the scenario will too — better
# to surface the actual error here than to debug TCP RSTs from inside the
# scenario script later.
log "reachability check: http://$GATEWAY_IP/log"
for attempt in $(seq 1 30); do
    if curl -sS -m 2 -o /dev/null -w 'http_code=%{http_code} time=%{time_total}s\n' \
            "http://$GATEWAY_IP/log" 2>&1 | tee -a /tmp/reachability.log; then
        log "  gateway reachable after $attempt attempt(s)"
        break
    fi
    sleep 0.5
    if [ "$attempt" -eq 30 ]; then
        log "  WARN: gateway never reachable in 15s"
        /usr/sbin/ip -4 addr show >&2
        /usr/sbin/ip -4 route show >&2
        /usr/sbin/ip neigh show >&2
    fi
done

# ─── 2b. Connectivity test stubs (wpa_supplicant + DHCP) ─────────────────
# DESK-002: setup_captive runs wpa_supplicant + a one-shot DHCP client INSIDE
# the gatepath netns. This "wlan0" is a veth with no real radio to associate,
# and neither client is installed — so provide test doubles, the same spirit as
# the dbusmock NetworkManager and the WebView runner stub. wpa_supplicant just
# stays alive for the session (bring_up spawns it fire-and-forget); the DHCP
# stub assigns the captive static lease the real server would have handed out
# and exits 0. The orchestration (move → bring_up → spawn → teardown) is what's
# under test here; real association/DHCP is the mac80211_hwsim / on-hardware job.
log "installing wpa_supplicant + dhclient test stubs"
cat >/usr/sbin/wpa_supplicant <<'STUB'
#!/bin/sh
# A real supplicant associates and stays running for the whole session.
exec sleep infinity
STUB
cat >/usr/sbin/dhclient <<'STUB'
#!/bin/sh
# One-shot DHCP double: we are already `ip netns exec`'d into the gatepath
# netns. Assign the captive static lease (replace = idempotent vs the runner's
# own setup) and exit 0 to mimic a successful lease. The iface is the last arg
# in the helper's dhcp argv.
iface=wlan0
for a in "$@"; do iface="$a"; done
/usr/sbin/ip addr replace 172.30.0.220/24 dev "$iface"
/usr/sbin/ip route replace default via 172.30.0.2 dev "$iface"
exit 0
STUB
chmod +x /usr/sbin/wpa_supplicant /usr/sbin/dhclient

# ─── 3. System D-Bus ─────────────────────────────────────────────────────
mkdir -p /run/dbus
dbus-uuidgen --ensure
log "starting system dbus-daemon"
dbus-daemon --system --nofork --nopidfile --print-address=2 &
DBUS_PID=$!

# Wait for the system bus socket to appear.
for _ in $(seq 1 50); do
    [ -S /run/dbus/system_bus_socket ] && break
    sleep 0.1
done
if [ ! -S /run/dbus/system_bus_socket ]; then
    log "FATAL: system bus socket never appeared"
    exit 1
fi

# ─── 4. polkitd ──────────────────────────────────────────────────────────
log "starting polkitd"
/usr/lib/polkit-1/polkitd --no-debug &
POLKIT_PID=$!

# polkitd needs a tick to claim org.freedesktop.PolicyKit1 on the bus.
for _ in $(seq 1 50); do
    if dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus.ListNames 2>/dev/null | grep -q PolicyKit1; then
        break
    fi
    sleep 0.1
done

# ─── 5. dbusmock NetworkManager ──────────────────────────────────────────
log "starting dbusmock NetworkManager"
python3 /opt/e2e/dbusmock_nm.py &
DBUSMOCK_PID=$!

# Wait for the mock to claim the name.
for _ in $(seq 1 50); do
    if dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus.ListNames 2>/dev/null | grep -q "org.freedesktop.NetworkManager"; then
        break
    fi
    sleep 0.1
done

# ─── 5b. Helper diagnostics: ldd + a 1s synchronous probe ─────────────────
# When the helper fails to claim its bus name we want to know WHY, not just
# that activation timed out. Capture link-time deps + a short synchronous
# run to surface any startup-time output (dlopen errors, panics, etc.).
mkdir -p /tmp/artifacts
log "helper ldd:"
ldd /usr/libexec/gatepath-netns-helper 2>&1 | tee /tmp/artifacts/helper-ldd.log | head -20 >&2 || true
log "synchronous 2s probe of helper (RUST_LOG=trace, expecting it to run until killed):"
(
    timeout 2 /usr/libexec/gatepath-netns-helper >/tmp/artifacts/helper-probe.log 2>&1
    echo "probe exit=$?" >> /tmp/artifacts/helper-probe.log
) &
wait $! || true
sed 's/^/  helper-probe: /' /tmp/artifacts/helper-probe.log >&2

# Now background-start it for real (so D-Bus activation finds it already
# running and bypasses the activation timeout).
log "pre-starting helper (RUST_LOG=debug)"
RUST_LOG=debug /usr/libexec/gatepath-netns-helper \
    > /tmp/artifacts/helper.log 2>&1 &
HELPER_PID=$!
sleep 0.3
if ! kill -0 "$HELPER_PID" 2>/dev/null; then
    log "  HELPER DIED — see /tmp/artifacts/helper.log + /tmp/artifacts/helper-probe.log"
fi

for _ in $(seq 1 100); do
    if dbus-send --system --print-reply --dest=org.freedesktop.DBus /org/freedesktop/DBus \
            org.freedesktop.DBus.ListNames 2>/dev/null | grep -q "com.ventouxlabs.Gatepath.NetNsHelper"; then
        log "  helper bus name claimed"
        break
    fi
    if ! kill -0 "$HELPER_PID" 2>/dev/null; then
        break
    fi
    sleep 0.1
done

# ─── 6. Xvfb ─────────────────────────────────────────────────────────────
export DISPLAY=":99"
log "starting Xvfb on $DISPLAY"
Xvfb :99 -screen 0 1280x800x24 -nolisten tcp &
XVFB_PID=$!

# Wait for the X socket. Xvfb creates /tmp/.X11-unix/X99.
for _ in $(seq 1 50); do
    [ -S /tmp/.X11-unix/X99 ] && break
    sleep 0.1
done

cleanup() {
    log "tearing down"
    for pid in "${XVFB_PID:-}" "${HELPER_PID:-}" "${DBUSMOCK_PID:-}" "${POLKIT_PID:-}" "${DBUS_PID:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    wait || true
}
trap cleanup INT TERM EXIT

# ─── 7. Dispatch ─────────────────────────────────────────────────────────
case "$MODE" in
    test)
        log "running scenario as tester (UID $TESTER_UID)"
        # The scenario needs DISPLAY (for the eventual WebView) and a
        # path to write its own state. Pass the system-bus address
        # explicitly so dasbus can reach it under the runuser drop.
        chown -R tester:tester /home/tester
        # The helper's audit log directory needs to be readable by the
        # scenario after the test for assertion reads — chgrp tester
        # so the inevitable 0640 file is reachable. Helper still owns
        # it (uid 0); tester reads via group.
        chgrp -R tester /var/lib/gatepath
        chmod g+rx /var/lib/gatepath
        set +e
        runuser -u tester -- env \
            DISPLAY="$DISPLAY" \
            DBUS_SYSTEM_BUS_ADDRESS="unix:path=/run/dbus/system_bus_socket" \
            PYTHONUNBUFFERED=1 \
            python3 /opt/e2e/run-scenario.py
        rc=$?
        set -e
        log "scenario finished with rc=$rc"

        # Persist artefacts to the host bind-mount before the container
        # exits. /tmp/artifacts is mounted from ./artifacts on the host
        # (compose.yml). chmod o+r so the host user can read them after
        # podman's user-namespace remap.
        mkdir -p /tmp/artifacts
        cp -f /tmp/scenario-report.json     /tmp/artifacts/ 2>/dev/null || true
        cp -f /tmp/scenario-screenshot.png  /tmp/artifacts/ 2>/dev/null || true
        cp -f /tmp/gateway-log.json         /tmp/artifacts/ 2>/dev/null || true
        # The in-netns no-leak probe is written by the runner (as tester) to a
        # tester-writable /tmp path; copy it out as root for the host-side
        # confinement assertion.
        cp -f /tmp/netns-sentinel-probe.json /tmp/artifacts/ 2>/dev/null || true
        cp -f /var/lib/gatepath/helper-audit.jsonl /tmp/artifacts/ 2>/dev/null || true
        chmod -R a+rX /tmp/artifacts || true

        exit "$rc"
        ;;
    wait)
        log "fixtures up; idling for podman exec"
        # Surface dbus-daemon's pid for child debugging
        sleep infinity
        ;;
    shell)
        exec bash
        ;;
    *)
        log "unknown mode: $MODE (expected test|wait|shell)"
        exit 2
        ;;
esac
