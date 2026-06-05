#!/usr/bin/env bash
# tests/e2e-hwsim/run.sh
#
# mac80211_hwsim end-to-end harness (ROADMAP P0.2). Drives the REAL privileged
# gatepath-netns-helper through the full desktop isolation path on two virtual
# radios, with NO real captive Wi-Fi, and proves the no-leak invariant:
#
#   AP radio  (gpap0, host netns) → open AP + dnsmasq + mock captive portal
#   client    (wlangp0)            → NetworkManager connects it; helper moves its
#                                  PHY into the `gatepath` netns, re-associates
#                                  in-netns, runs DHCP, spawns the runner
#   sentinel  (gpsen0, host)     → trusted-net stand-in the netns MUST NOT reach
#
# Run AFTER building the helper:
#   bash tests/e2e-hwsim/build-helper.sh      # as your normal user
#   sudo bash tests/e2e-hwsim/run.sh          # this script, as root
#
# This is the privileged half of the harness; it cannot run in the Claude
# sandbox (no netns/module privilege). It is NOT `set -e`: every failure is
# handled explicitly and an unconditional EXIT trap tears everything down.
#
# Flags:
#   --dhcp static|real   DHCP client behaviour inside the netns (default: static)
#   --webview            install the marker so the runner execs the real WebKit
#                        WebView (needs a graphical session; default: headless)
#   --keep               skip teardown on exit (leave radios/netns up to inspect)
#   --teardown-only      run only the cleanup for a previous (possibly crashed) run
#   --help

set -u
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

# ── Flags ────────────────────────────────────────────────────────────────
DHCP_MODE="static"
WEBVIEW=0
KEEP=0
TEARDOWN_ONLY=0

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit 0; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dhcp) DHCP_MODE="${2:-}"; shift 2 || true ;;
    --dhcp=*) DHCP_MODE="${1#*=}"; shift ;;
    --webview) WEBVIEW=1; shift ;;
    --keep) KEEP=1; shift ;;
    --teardown-only) TEARDOWN_ONLY=1; shift ;;
    -h|--help) usage ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done
case "$DHCP_MODE" in
  static|real) ;;
  *) die "--dhcp must be 'static' or 'real', got '$DHCP_MODE'" ;;
esac

# ── Run state (consumed by cleanup) ──────────────────────────────────────
WORKDIR=""
LOADED_HWSIM=0          # did WE modprobe it? only then do we unload
AP_WPA_PID=""
DNSMASQ_PID=""
MOCKPORTAL_PID=""
SENTINEL_PID=""
HELPER_PID=""
NM_CONN_DROPIN="/etc/NetworkManager/conf.d/99-gatepath-hwsim-connectivity.conf"
DBUS_CONF_DST="/etc/dbus-1/system.d/${DBUS_NAME}.conf"
POLKIT_RULE_DST="/etc/polkit-1/rules.d/49-gatepath-hwsim.rules"
NFT_TABLE="inet gatepath_hwsim"
INSTALLED_DBUS_CONF=0
INSTALLED_POLKIT=0
INSTALLED_NM_DROPIN=0
INSTALLED_NFT=0
NM_CONN_PROFILE="$SSID"

# ── Cleanup (unconditional EXIT trap) ────────────────────────────────────
cleanup() {
  local rc=$?
  if [ "$KEEP" -eq 1 ] && [ "$TEARDOWN_ONLY" -eq 0 ]; then
    warn "--keep set: leaving radios, netns, and services up. Re-run with"
    warn "  sudo bash tests/e2e-hwsim/run.sh --teardown-only"
    warn "to clean up later."
    return
  fi
  hdr "teardown"

  # Best-effort helper teardown first so the kernel pops the PHY back, then
  # stop the helper itself.
  if busctl_name_has_owner; then
    log "TeardownCaptive() via D-Bus"
    busctl call "$DBUS_NAME" "$DBUS_OBJ" "$DBUS_IFACE" TeardownCaptive >/dev/null 2>&1 || true
  fi
  kill_pid "$HELPER_PID" "helper"
  pkill -f "$HELPER_BIN" 2>/dev/null || true

  # Stop AP-side services.
  kill_pid "$MOCKPORTAL_PID" "mockportal"
  kill_pid "$DNSMASQ_PID" "dnsmasq"
  kill_pid "$SENTINEL_PID" "sentinel"
  kill_pid "$AP_WPA_PID" "ap wpa_supplicant"
  pkill -f "mockportal.server" 2>/dev/null || true
  pkill -f "http.server.*$SENTINEL_ADDR" 2>/dev/null || true
  pkill -f "dnsmasq.*$AP_IFACE" 2>/dev/null || true
  pkill -f "wpa_supplicant.*$AP_IFACE" 2>/dev/null || true

  # NetworkManager bits.
  nmcli connection delete "$NM_CONN_PROFILE" >/dev/null 2>&1 || true
  if [ "$INSTALLED_NM_DROPIN" -eq 1 ] || [ -f "$NM_CONN_DROPIN" ]; then
    rm -f "$NM_CONN_DROPIN" 2>/dev/null || true
    nm_reload
    log "removed NM connectivity drop-in"
  fi

  # netns (also reclaims the in-netns client PHY before we unload hwsim).
  if ip netns list 2>/dev/null | grep -q "^${NETNS}\b"; then
    ip netns del "$NETNS" 2>/dev/null || true
  fi

  # Sentinel dummy link.
  ip link del "$SENTINEL_IFACE" 2>/dev/null || true

  # nftables / iptables forward block.
  if [ "$INSTALLED_NFT" -eq 1 ] || { have nft && nft list table $NFT_TABLE >/dev/null 2>&1; }; then
    have nft && nft delete table $NFT_TABLE 2>/dev/null || true
    have iptables && iptables -D FORWARD -i "$AP_IFACE" -j DROP 2>/dev/null || true
  fi

  # Unload hwsim only if we loaded it (reclaims gpap0/wlangp0 radios).
  if [ "$LOADED_HWSIM" -eq 1 ] || [ "$TEARDOWN_ONLY" -eq 1 ]; then
    modprobe -r mac80211_hwsim 2>/dev/null \
      || warn "could not unload mac80211_hwsim (in use); clears on reboot or: rmmod -f mac80211_hwsim"
  fi

  # D-Bus + polkit system files.
  if [ "$INSTALLED_POLKIT" -eq 1 ] || [ -f "$POLKIT_RULE_DST" ]; then
    rm -f "$POLKIT_RULE_DST" 2>/dev/null || true
  fi
  if [ "$INSTALLED_DBUS_CONF" -eq 1 ] || [ -f "$DBUS_CONF_DST" ]; then
    rm -f "$DBUS_CONF_DST" 2>/dev/null || true
    busctl call org.freedesktop.DBus / org.freedesktop.DBus ReloadConfig >/dev/null 2>&1 || true
  fi

  # Runner + marker (leave /var/lib/gatepath audit log in place for inspection).
  rm -f "$WEBVIEW_MARKER" 2>/dev/null || true

  # Preserve the workdir (all the logs) when the run FAILED so it can be
  # diagnosed; only clean it up on a clean pass.
  if [ -n "$WORKDIR" ]; then
    if [ "$rc" -eq 0 ]; then
      rm -rf "$WORKDIR" 2>/dev/null || true
    else
      warn "run did not pass — logs preserved at: $WORKDIR"
      warn "  (helper.log, wpa-ap.log, dnsmasq.log, nmcli-connect.log, mockportal.log)"
    fi
  fi
  log "teardown complete"
  exit "$rc"
}
trap cleanup EXIT

# ── small helpers ────────────────────────────────────────────────────────
kill_pid() {
  local pid="$1" label="$2"
  [ -n "$pid" ] || return 0
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    log "stopped $label (pid $pid)"
  fi
}

busctl_name_has_owner() {
  local out
  out="$(busctl call org.freedesktop.DBus /org/freedesktop/DBus \
         org.freedesktop.DBus NameHasOwner s "$DBUS_NAME" 2>/dev/null)" || return 1
  [ "$out" = "b true" ]
}

# PID currently owning the helper bus name (empty if none).
bus_owner_pid() {
  local out
  out="$(busctl call org.freedesktop.DBus /org/freedesktop/DBus \
         org.freedesktop.DBus GetConnectionUnixProcessID s "$DBUS_NAME" 2>/dev/null)" || return 1
  printf '%s' "$out" | awk '{print $2}'
}

nm_reload() {
  if have systemctl && systemctl is-active --quiet NetworkManager; then
    systemctl reload NetworkManager 2>/dev/null && return 0
  fi
  nmcli general reload 2>/dev/null || true
}

wait_for() { # wait_for <timeout_s> <description> <cmd...>
  local timeout="$1" desc="$2"; shift 2
  local deadline=$(( SECONDS + timeout ))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if "$@" >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  warn "timed out after ${timeout}s waiting for: $desc"
  return 1
}

# ── teardown-only short-circuit ──────────────────────────────────────────
if [ "$TEARDOWN_ONLY" -eq 1 ]; then
  [ "$(id -u)" -eq 0 ] || die "must be root for --teardown-only"
  log "teardown-only: cleaning up any prior hwsim-harness state"
  # cleanup() runs via the EXIT trap; force the unload path on.
  exit 0
fi

# ═════════════════════════════════════════════════════════════════════════
#  Preconditions
# ═════════════════════════════════════════════════════════════════════════
hdr "0. preconditions"
[ "$(id -u)" -eq 0 ] || die "run.sh must be root (sudo bash tests/e2e-hwsim/run.sh)"

[ -x "$HELPER_BIN" ] || die "helper binary not found at $HELPER_BIN — run: bash tests/e2e-hwsim/build-helper.sh"
ok "helper binary present: $HELPER_BIN"

for t in iw wpa_supplicant dnsmasq nmcli ip python3 busctl modprobe; do
  have "$t" || die "required tool missing: $t"
done
have curl || warn "curl not found — the in-netns runner needs it; install curl"
ok "required tools present"

if [ "$DHCP_MODE" = "real" ]; then
  have udhcpc || have busybox \
    || die "--dhcp real needs busybox/udhcpc on PATH; use --dhcp static (default) instead"
fi

if ! systemctl is-active --quiet NetworkManager 2>/dev/null; then
  die "NetworkManager is not active; the helper requires it (Device.Connectivity)"
fi
ok "NetworkManager active"

if [ -f /run/.containerenv ] || [ -f /.dockerenv ]; then
  warn "looks like a container — netns/module ops may be denied; bare metal is expected"
fi

WORKDIR="$(mktemp -d /tmp/gatepath-hwsim.XXXXXX)" || die "mktemp failed"
mkdir -p "$WORKDIR/bin"
log "workdir: $WORKDIR"

# Pre-clean wedged state from a prior crashed run so we fail at REAL problems,
# not leftovers: a stale helper still owns the bus name, and the helper's
# create_netns refuses if the `gatepath` netns already exists.
if busctl_name_has_owner; then
  spid="$(bus_owner_pid)"
  warn "stale process owns $DBUS_NAME (pid ${spid:-?}) — killing it before we start"
  [ -n "$spid" ] && kill "$spid" 2>/dev/null || true
  pkill -f "$HELPER_BIN" 2>/dev/null || true
  for _ in $(seq 1 5); do busctl_name_has_owner || break; sleep 1; done
  busctl_name_has_owner && die "could not free $DBUS_NAME; run --teardown-only, or reboot if it persists"
fi
if ip netns list 2>/dev/null | grep -q "^${NETNS}\b"; then
  warn "stale netns '$NETNS' present — removing it (helper refuses a pre-existing netns)"
  ip netns del "$NETNS" 2>/dev/null || true
fi

# ═════════════════════════════════════════════════════════════════════════
#  1. Virtual radios
# ═════════════════════════════════════════════════════════════════════════
hdr "1. virtual radios (mac80211_hwsim)"
iw reg set US 2>/dev/null || true

# Snapshot existing hwsim netdevs so we only ever claim radios WE create and
# never disturb a real phy0 the box may have.
hwsim_netdevs() {
  local d drv
  for d in /sys/class/net/*; do
    [ -e "$d/phy80211/name" ] || continue
    drv="$(basename "$(readlink -f "$d/device/driver" 2>/dev/null)" 2>/dev/null || true)"
    [ "$drv" = "mac80211_hwsim" ] && basename "$d"
  done
}

mapfile -t PRE_HWSIM < <(hwsim_netdevs)
if lsmod 2>/dev/null | grep -q '^mac80211_hwsim'; then
  warn "mac80211_hwsim already loaded — reusing existing virtual radios, NOT unloading on exit"
  mapfile -t NEW_HWSIM < <(hwsim_netdevs)
else
  modprobe mac80211_hwsim radios=2 2>"$WORKDIR/modprobe.err" \
    || die "modprobe mac80211_hwsim radios=2 failed: $(cat "$WORKDIR/modprobe.err")"
  LOADED_HWSIM=1
  # Settle: udev/NM enumerate the new netdevs. Poll in-shell (the helper
  # function isn't available to a `bash -c` subshell).
  for _ in $(seq 1 10); do
    [ "$(hwsim_netdevs | wc -l)" -ge 2 ] && break
    sleep 1
  done
  # NEW = post − pre
  mapfile -t ALL_HWSIM < <(hwsim_netdevs)
  NEW_HWSIM=()
  for n in "${ALL_HWSIM[@]}"; do
    skip=0
    for p in "${PRE_HWSIM[@]:-}"; do [ "$n" = "$p" ] && skip=1; done
    [ "$skip" -eq 0 ] && NEW_HWSIM+=("$n")
  done
fi

[ "${#NEW_HWSIM[@]}" -ge 2 ] || die "need 2 hwsim radios, found ${#NEW_HWSIM[@]}: ${NEW_HWSIM[*]:-none}"
RAW_AP="${NEW_HWSIM[0]}"
RAW_CL="${NEW_HWSIM[1]}"
log "claiming hwsim radios: AP=$RAW_AP  client=$RAW_CL"

# Release from NM, down, rename to unambiguous names.
rename_iface() {
  local from="$1" to="$2"
  [ "$from" = "$to" ] && return 0
  nmcli device set "$from" managed no >/dev/null 2>&1 || true
  ip link set "$from" down 2>/dev/null || true
  ip link set "$from" name "$to" 2>/dev/null \
    || die "could not rename $from → $to (busy?)"
}
rename_iface "$RAW_AP" "$AP_IFACE"
rename_iface "$RAW_CL" "$CL_IFACE"
ok "radios renamed: $AP_IFACE (AP), $CL_IFACE (client)"

# ═════════════════════════════════════════════════════════════════════════
#  2. Open AP + DHCP/DNS + mock portal
# ═════════════════════════════════════════════════════════════════════════
hdr "2. open AP, DHCP/DNS, mock captive portal"
# hwsim radios often come up SOFT-BLOCKED by rfkill, and NM may have wifi
# disabled — either one silently breaks BOTH AP-enable and client scanning.
# Clear them before we touch the radios.
have rfkill && rfkill unblock all 2>/dev/null || true
nmcli radio wifi on 2>/dev/null || true
nmcli device set "$AP_IFACE" managed no >/dev/null 2>&1 || true

cat > "$WORKDIR/ap.conf" <<EOF
country=US
ctrl_interface=DIR=$WORKDIR/wpa-ap
network={
    ssid="$SSID"
    mode=2
    frequency=$AP_CHANNEL_FREQ
    key_mgmt=NONE
}
EOF
# Bring the iface up before handing it to wpa_supplicant (some builds won't
# transition a down iface into AP mode), and run wpa_supplicant verbose (-dd)
# so a failure to enable the AP is visible in wpa-ap.log.
ip link set "$AP_IFACE" up 2>/dev/null || true
wpa_supplicant -i "$AP_IFACE" -D nl80211 -c "$WORKDIR/ap.conf" -dd \
  >"$WORKDIR/wpa-ap.log" 2>&1 &
AP_WPA_PID=$!

# Process-alive is NOT enough: wpa_supplicant can stay up but fail to enable the
# AP (bad channel / regdomain / no-IR). Wait for it to ACTUALLY beacon — the
# interface flips to "type AP" and the log prints AP-ENABLED — before we trust it.
ap_ready=0
for _ in $(seq 1 15); do
  kill -0 "$AP_WPA_PID" 2>/dev/null || break
  if iw dev "$AP_IFACE" info 2>/dev/null | grep -qi 'type AP' \
     || grep -q 'AP-ENABLED' "$WORKDIR/wpa-ap.log" 2>/dev/null; then
    ap_ready=1; break
  fi
  sleep 1
done
if [ "$ap_ready" -ne 1 ]; then
  err "AP did NOT start beaconing on $AP_IFACE (wpa_supplicant AP-mode failed)."
  err "wpa-ap.log tail (-dd):"; tail -n 45 "$WORKDIR/wpa-ap.log" 2>/dev/null | sed 's/^/      /' >&2
  err "key AP/mode/error lines from the log:"
  grep -iE 'AP-|iftype|interface state|Mode:|channel|freq|Failed|Could not|not (allowed|permitted)|nl80211.*(fail|error)|country|select_network|disabled' \
    "$WORKDIR/wpa-ap.log" 2>/dev/null | tail -n 25 | sed 's/^/      /' >&2
  err "iface (iw dev $AP_IFACE info):"; iw dev "$AP_IFACE" info 2>/dev/null | sed 's/^/      /' >&2
  err "rfkill:"; { have rfkill && rfkill list 2>/dev/null || echo "(rfkill not installed)"; } | sed 's/^/      /' >&2
  die "AP failed to enable — see the wpa-ap.log lines above (full log: $WORKDIR/wpa-ap.log)"
fi

ip addr replace "$AP_CIDR" dev "$AP_IFACE" || die "could not set AP address"
ok "AP beaconing on $AP_IFACE ($AP_ADDR), SSID '$SSID'"

dnsmasq --keep-in-foreground --bind-interfaces --interface="$AP_IFACE" \
  --no-resolv --no-hosts \
  --dhcp-range="$DHCP_RANGE_LO,$DHCP_RANGE_HI,255.255.255.0,12h" \
  --dhcp-option=3,"$AP_ADDR" --dhcp-option=6,"$AP_ADDR" \
  --address="/#/$AP_ADDR" \
  --pid-file="$WORKDIR/dnsmasq.pid" \
  >"$WORKDIR/dnsmasq.log" 2>&1 &
DNSMASQ_PID=$!
sleep 1
kill -0 "$DNSMASQ_PID" 2>/dev/null || die "dnsmasq died; see $WORKDIR/dnsmasq.log"
ok "dnsmasq serving DHCP/DNS on $AP_IFACE (wildcard DNS → $AP_ADDR)"

# Mock captive portal on the AP gateway. complete_after huge so it stays
# captive (never auto-validates) for the whole run — the helper's is_captive
# gate needs NM to keep flagging PORTAL until SetupCaptive.
( cd "$REPO_ROOT" && \
  PORTAL_HOST="$AP_ADDR" PORTAL_PORT="$PORTAL_PORT" PORTAL_COMPLETE_AFTER=1000000 \
  python3 -m mockportal.server >"$WORKDIR/mockportal.log" 2>&1 ) &
MOCKPORTAL_PID=$!
wait_for 10 "mock portal on $AP_ADDR:$PORTAL_PORT" \
  curl -sS -m 2 -o /dev/null "http://$AP_ADDR:$PORTAL_PORT/portal" \
  || die "mock portal never came up; see $WORKDIR/mockportal.log"
ok "mock captive portal up at $PORTAL_URL"

# ═════════════════════════════════════════════════════════════════════════
#  3. Trusted-net sentinel (host-only) + confinement belt
# ═════════════════════════════════════════════════════════════════════════
hdr "3. trusted-net sentinel + forward block"
ip link add "$SENTINEL_IFACE" type dummy 2>/dev/null || true
ip addr replace "$SENTINEL_CIDR" dev "$SENTINEL_IFACE"
ip link set "$SENTINEL_IFACE" up
mkdir -p "$WORKDIR/sentinel-root"
printf 'gatepath-hwsim-sentinel-ok\n' > "$WORKDIR/sentinel-root/health"
python3 -m http.server "$PORTAL_PORT" --bind "$SENTINEL_ADDR" \
  --directory "$WORKDIR/sentinel-root" >"$WORKDIR/sentinel.log" 2>&1 &
SENTINEL_PID=$!
wait_for 10 "sentinel on $SENTINEL_ADDR" \
  curl -sS -m 2 -o /dev/null "$SENTINEL_URL" \
  || die "sentinel never came up; see $WORKDIR/sentinel.log"
ok "sentinel reachable from host: $SENTINEL_URL"

# Belt-and-suspenders: drop anything the AP link tries to ROUTE onward, so the
# netns can't reach the host-local sentinel even if ip_forward=1 globally.
# (Portal traffic is delivered locally on the AP, never forwarded, so this is
# safe.) Models a real captive AP that gives you a link but doesn't route you.
if have nft; then
  nft add table $NFT_TABLE 2>/dev/null && INSTALLED_NFT=1 || true
  nft 'add chain '"$NFT_TABLE"' fwd { type filter hook forward priority -150 ; policy accept ; }' 2>/dev/null || true
  nft add rule $NFT_TABLE fwd iifname "$AP_IFACE" drop 2>/dev/null || true
  [ "$INSTALLED_NFT" -eq 1 ] && ok "nftables forward block on $AP_IFACE installed"
elif have iptables; then
  iptables -I FORWARD -i "$AP_IFACE" -j DROP 2>/dev/null && INSTALLED_NFT=1 \
    && ok "iptables forward block on $AP_IFACE installed" || true
else
  fwd="$(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo '?')"
  warn "no nft/iptables to install a forward block (ip_forward=$fwd)."
  warn "if ip_forward=1 the confinement result may be unreliable."
fi

# ═════════════════════════════════════════════════════════════════════════
#  4. Connect the client via NetworkManager → PORTAL
# ═════════════════════════════════════════════════════════════════════════
hdr "4. NetworkManager connects the client"
# Point NM's connectivity check at the portal's /generate_204 (302 → unambiguous
# PORTAL) so the helper's is_captive gate (Device.Connectivity==PORTAL) passes.
# This is global + restored right after SetupCaptive (and in cleanup). During the
# window other interfaces may briefly flag captive — see README "side effects".
cat > "$NM_CONN_DROPIN" <<EOF
[connectivity]
enabled=true
uri=http://$AP_ADDR/generate_204
interval=5
EOF
INSTALLED_NM_DROPIN=1
nm_reload
log "NM connectivity check pointed at http://$AP_ADDR/generate_204 (global, temporary)"

nmcli device set "$CL_IFACE" managed yes >/dev/null 2>&1 || true
nmcli device wifi rescan ifname "$CL_IFACE" >/dev/null 2>&1 || true
if ! wait_for 25 "SSID '$SSID' to appear in scan" \
  bash -c "nmcli -t -f SSID device wifi list ifname '$CL_IFACE' 2>/dev/null | grep -qx '$SSID'"; then
  # Disambiguate "AP not beaconing" from "NM not scanning" by scanning at the
  # driver level, bypassing NM. If `iw` sees the SSID but NM doesn't, it's an NM
  # problem; if neither does, the RF link / AP beaconing is the problem.
  warn "SSID not seen via NM. Driver-level scan on $CL_IFACE (iw):"
  ip link set "$CL_IFACE" up 2>/dev/null || true
  iw dev "$CL_IFACE" scan 2>&1 | grep -iE 'SSID|freq|signal|BSS ' | head -n 20 | sed 's/^/      /' >&2 || true
  warn "radios overview (iw dev):"
  iw dev 2>/dev/null | grep -iE 'Interface|type|channel|ssid|addr' | sed 's/^/      /' >&2 || true
  warn "AP wpa_supplicant tail:"
  tail -n 12 "$WORKDIR/wpa-ap.log" 2>/dev/null | sed 's/^/      /' >&2 || true
  warn "attempting NM connect anyway"
fi

# Drop any stale profile from a prior run so connect starts clean.
nmcli connection delete "$SSID" >/dev/null 2>&1 || true
if ! nmcli device wifi connect "$SSID" ifname "$CL_IFACE" >"$WORKDIR/nmcli-connect.log" 2>&1; then
  warn "nmcli connect error:"; sed 's/^/      /' "$WORKDIR/nmcli-connect.log" >&2
  warn "BSS seen by NM (nmcli dev wifi list):"
  nmcli -f SSID,BSSID,CHAN,SIGNAL,SECURITY device wifi list ifname "$CL_IFACE" 2>/dev/null \
    | grep -iE "SSID|$SSID" | sed 's/^/      /' >&2 || true
fi
wait_for 30 "$CL_IFACE to associate" \
  bash -c "nmcli -g GENERAL.STATE device show '$CL_IFACE' 2>/dev/null | grep -q '100'" \
  || warn "client did not reach state=connected; continuing to portal poll"

if wait_for 40 "NM to flag $CL_IFACE as PORTAL" \
   bash -c "nmcli -g GENERAL.CONNECTIVITY device show '$CL_IFACE' 2>/dev/null | grep -qx portal"; then
  ok "$CL_IFACE connected, NM connectivity = portal"
else
  warn "NM did not flag $CL_IFACE=portal. Current device state:"
  nmcli device show "$CL_IFACE" 2>/dev/null | sed 's/^/      /' >&2 || true
  warn "SetupCaptive will likely be refused with NotCaptive. See README troubleshooting."
fi

# ═════════════════════════════════════════════════════════════════════════
#  5. Install helper runtime artifacts
# ═════════════════════════════════════════════════════════════════════════
hdr "5. install helper artifacts"
mkdir -p "$HELPER_STATE_DIR" "$RUNNER_INSTALL_DIR" "$HELPER_RUNTIME_DIR"

install -m 0755 "$HWSIM_DIR/portal-webview-runner.hwsim" "$RUNNER_INSTALL_PATH"
ok "runner installed at $RUNNER_INSTALL_PATH"
if [ "$WEBVIEW" -eq 1 ]; then
  : > "$WEBVIEW_MARKER"
  log "webview marker set — runner will exec the real WebKit WebView"
fi

# System-bus policy so root can own the helper's name on the REAL system bus.
install -m 0644 "$CRATE_DIR/data/${DBUS_NAME}.conf" "$DBUS_CONF_DST"
INSTALLED_DBUS_CONF=1
busctl call org.freedesktop.DBus / org.freedesktop.DBus ReloadConfig >/dev/null 2>&1 || true
ok "D-Bus system policy installed + reloaded"

# PolicyKit: the shipped .policy lives under read-only /usr/share, so a rules.d
# rule (writable) authorizes both action ids regardless of action registration.
cat > "$POLKIT_RULE_DST" <<'EOF'
// hwsim harness only — auto-allow Gatepath helper actions (no auth agent on a
// headless test box). Removed on teardown; never installed on real systems.
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("cc.grepon.Gatepath.NetNsHelper.") === 0) {
        return polkit.Result.YES;
    }
});
EOF
INSTALLED_POLKIT=1
ok "polkit YES rule installed at $POLKIT_RULE_DST"

# DHCP client shim the helper will exec inside the netns. The helper's argv is
# `udhcpc -f -q -n -t 6 -i <iface>` (no -s); our shim supplies the rest.
if [ "$DHCP_MODE" = "static" ]; then
  cat > "$WORKDIR/bin/udhcpc" <<EOF
#!/bin/sh
# static-lease shim: pin the lease and exit 0 (helper waits on this).
iface=""; prev=""
for a in "\$@"; do [ "\$prev" = "-i" ] && iface="\$a"; prev="\$a"; done
[ -n "\$iface" ] || iface="$CL_IFACE"
ip addr replace "$CLIENT_STATIC_CIDR" dev "\$iface"
ip route replace default via "$AP_ADDR" dev "\$iface"
exit 0
EOF
else
  cat > "$WORKDIR/bin/udhcpc.script" <<'EOF'
#!/bin/sh
case "$1" in
  deconfig) ip addr flush dev "$interface" 2>/dev/null || true ;;
  bound|renew)
    ip addr replace "$ip/${mask:-24}" dev "$interface"
    [ -n "${router:-}" ] && ip route replace default via "$router" dev "$interface"
    ;;
esac
exit 0
EOF
  chmod +x "$WORKDIR/bin/udhcpc.script"
  realudhcpc="$(command -v udhcpc || true)"
  cat > "$WORKDIR/bin/udhcpc" <<EOF
#!/bin/sh
# real-DHCP shim: inject the -s script the helper's argv omits.
exec ${realudhcpc:-busybox udhcpc} "\$@" -s "$WORKDIR/bin/udhcpc.script"
EOF
fi
chmod +x "$WORKDIR/bin/udhcpc"
ok "DHCP shim ($DHCP_MODE) installed in $WORKDIR/bin"

# ═════════════════════════════════════════════════════════════════════════
#  6. Launch the helper as root on the real system bus
# ═════════════════════════════════════════════════════════════════════════
hdr "6. launch helper"
# Sanity: under the helper's PATH, `udhcpc` must resolve to OUR shim, not a
# system client — otherwise the in-netns DHCP runs a real client against the
# fake AP and SetupCaptive fails at the DHCP wait.
_resolved="$(PATH="$WORKDIR/bin:/usr/sbin:/usr/bin:/sbin:/bin" command -v udhcpc 2>/dev/null || true)"
[ "$_resolved" = "$WORKDIR/bin/udhcpc" ] \
  || warn "udhcpc resolves to '${_resolved:-<none>}', not the shim — in-netns DHCP may misbehave"

GATEPATH_LOG="${GATEPATH_LOG:-debug}" \
GATEPATH_DHCP_CLIENT=udhcpc \
PATH="$WORKDIR/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  "$HELPER_BIN" >"$WORKDIR/helper.log" 2>&1 &
HELPER_PID=$!
if wait_for 15 "helper to own $DBUS_NAME" busctl_name_has_owner; then
  ok "helper owns $DBUS_NAME (pid $HELPER_PID)"
else
  err "helper did not claim the bus name. Last log lines:"
  tail -n 30 "$WORKDIR/helper.log" 2>/dev/null | sed 's/^/      /' >&2
  die "helper startup failed"
fi

# ═════════════════════════════════════════════════════════════════════════
#  7. Drive the privileged path
# ═════════════════════════════════════════════════════════════════════════
hdr "7. drive SetupCaptive → LaunchPortal → TeardownCaptive"
PASS=1
note_fail() { err "$1"; PASS=0; }

# --- SetupCaptive(client iface) → netns path ---
if setup_out="$(busctl call "$DBUS_NAME" "$DBUS_OBJ" "$DBUS_IFACE" \
                SetupCaptive s "$CL_IFACE" 2>"$WORKDIR/setup.err")"; then
  netns_path="$(printf '%s' "$setup_out" | awk -F'"' '{print $2}')"
  ok "SetupCaptive → $netns_path"
  [ "$netns_path" = "$NETNS_PATH" ] || note_fail "unexpected netns path: $netns_path (want $NETNS_PATH)"
else
  err "SetupCaptive refused: $(cat "$WORKDIR/setup.err")"
  err "helper log tail:"; tail -n 20 "$WORKDIR/helper.log" | sed 's/^/      /' >&2
  die "cannot proceed without a netns"
fi

# Connectivity check no longer needed; remove the global override promptly.
if [ "$INSTALLED_NM_DROPIN" -eq 1 ]; then
  rm -f "$NM_CONN_DROPIN" 2>/dev/null || true
  INSTALLED_NM_DROPIN=0
  nm_reload
  log "restored NM connectivity config"
fi

# Structural assertions on the netns.
if ip netns list 2>/dev/null | grep -q "^${NETNS}\b"; then
  ok "netns '$NETNS' exists"
else
  note_fail "netns '$NETNS' missing after SetupCaptive"
fi
if ip netns exec "$NETNS" iw dev 2>/dev/null | grep -q Interface; then
  ok "a wireless interface is present inside the netns"
else
  note_fail "no wireless interface inside the netns (PHY move failed?)"
fi

# --- LaunchPortal(url, "", "", "") → pid ---
rm -f "$RUNNER_VERDICT"
if launch_out="$(busctl call "$DBUS_NAME" "$DBUS_OBJ" "$DBUS_IFACE" \
                 LaunchPortal ssss "$PORTAL_URL" "" "" "" 2>"$WORKDIR/launch.err")"; then
  portal_pid="$(printf '%s' "$launch_out" | awk '{print $2}')"
  ok "LaunchPortal → pid $portal_pid"
else
  note_fail "LaunchPortal refused: $(cat "$WORKDIR/launch.err")"
fi

# --- Wait for the runner's no-leak verdict ---
if wait_for 20 "runner verdict at $RUNNER_VERDICT" test -s "$RUNNER_VERDICT"; then
  log "runner verdict:"; sed 's/^/      /' "$RUNNER_VERDICT" >&2
  s_reach="$(jq -r '.sentinel_reachable' "$RUNNER_VERDICT" 2>/dev/null)"
  p_code="$(jq -r '.portal_http_code' "$RUNNER_VERDICT" 2>/dev/null)"
  p_rc="$(jq -r '.portal_curl_rc' "$RUNNER_VERDICT" 2>/dev/null)"
  if [ "$s_reach" = "false" ]; then
    ok "NO-LEAK: sentinel UNREACHABLE from inside the netns (confined)"
  else
    note_fail "LEAK: netns reached the trusted-net sentinel (sentinel_reachable=$s_reach)"
  fi
  if [ "$p_rc" = "0" ]; then
    ok "portal reachable from inside the netns (http $p_code)"
  else
    note_fail "portal NOT reachable from inside the netns (curl rc=$p_rc, http $p_code)"
  fi
else
  note_fail "runner never wrote a verdict; helper log tail:"
  tail -n 20 "$WORKDIR/helper.log" | sed 's/^/      /' >&2
fi

# --- TeardownCaptive() ---
if busctl call "$DBUS_NAME" "$DBUS_OBJ" "$DBUS_IFACE" TeardownCaptive >/dev/null 2>"$WORKDIR/teardown.err"; then
  ok "TeardownCaptive → ok"
else
  note_fail "TeardownCaptive refused: $(cat "$WORKDIR/teardown.err")"
fi
if ip netns list 2>/dev/null | grep -q "^${NETNS}\b"; then
  note_fail "netns '$NETNS' still present after teardown"
else
  ok "netns torn down"
fi

# ═════════════════════════════════════════════════════════════════════════
#  Verdict
# ═════════════════════════════════════════════════════════════════════════
hdr "result"
if [ "$PASS" -eq 1 ]; then
  ok "PASS — full privileged path + no-leak confinement proven on mac80211_hwsim"
  log "Update docs/ROADMAP.md P0.1/P0.2 + docs/BLOCKERS.md only after a real green run."
  EXIT_RC=0
else
  err "FAIL — see the notes above and $WORKDIR/*.log (kept if --keep)"
  EXIT_RC=1
fi
# Hand the exit code to the trap (cleanup preserves $?).
exit "$EXIT_RC"
