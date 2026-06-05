# shellcheck shell=bash
# tests/e2e-hwsim/lib.sh
#
# Shared constants + logging for the mac80211_hwsim validation harness
# (ROADMAP P0.2). Sourced by build-helper.sh (run as a normal user) and
# run.sh (run as root). Sourcing this file must have NO side effects — it
# only defines variables and functions.
#
# The harness stands up two virtual radios with mac80211_hwsim: one acts as
# an open AP serving a mock captive portal, the other is the "client" that
# NetworkManager connects and that the REAL privileged helper then moves into
# the throwaway `gatepath` netns. A host-only sentinel proves the no-leak
# invariant: reachable from the host, and it MUST NOT be reachable from inside
# the netns. See README.md for the full picture.

# ── Repo layout ──────────────────────────────────────────────────────────
# This file lives at <repo>/tests/e2e-hwsim/lib.sh.
HWSIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HWSIM_DIR/../.." && pwd)"
CRATE_DIR="$REPO_ROOT/desktop/gatepath-netns-helper"
# Where `cargo build --release` drops the helper binary.
HELPER_BIN="$CRATE_DIR/target/release/gatepath-netns-helper"

# ── The netns the helper owns (hardcoded in the helper; we only observe it) ─
NETNS="gatepath"
NETNS_PATH="/var/run/netns/$NETNS"

# ── Virtual-radio netdev names ───────────────────────────────────────────
# Renamed off the kernel's wlanN so ownership is unambiguous and we never
# touch a real phy0/wlan0 the box might have.
AP_IFACE="gpap0"    # open AP side (stays in the host netns)
CL_IFACE="gpcl0"    # client side (the helper moves this into the netns)
SENTINEL_IFACE="gpsen0"  # dummy link carrying the host-only sentinel

# ── Addressing ───────────────────────────────────────────────────────────
SSID="GatepathHwsim"
AP_SUBNET="192.168.77.0/24"
AP_ADDR="192.168.77.1"           # AP gateway + DHCP/DNS + mock portal
AP_CIDR="192.168.77.1/24"
DHCP_RANGE_LO="192.168.77.10"
DHCP_RANGE_HI="192.168.77.100"
CLIENT_STATIC_CIDR="192.168.77.50/24"  # static-DHCP lease for the client
AP_CHANNEL_FREQ="2412"           # 2.4GHz ch1; both radios share it over hwsim
PORTAL_PORT="80"
PORTAL_URL="http://$AP_ADDR/portal"

# The sentinel models the user's TRUSTED network — the netns must never reach
# it. A host-only dummy link on an address the box is unlikely to use.
SENTINEL_ADDR="10.123.0.1"
SENTINEL_CIDR="10.123.0.1/24"
SENTINEL_URL="http://$SENTINEL_ADDR/health"

# ── Install paths the helper reads at runtime ────────────────────────────
# The helper refuses to start unless the runner exists at its compile-time
# PORTAL_RUNNER_PATH. build-helper.sh bakes THIS path in via
# GATEPATH_PORTAL_RUNNER_PATH; run.sh installs the runner here before launch.
# /var/lib is writable on immutable hosts; /usr is not.
RUNNER_INSTALL_DIR="/var/lib/gatepath/hwsim"
RUNNER_INSTALL_PATH="$RUNNER_INSTALL_DIR/portal-webview-runner"
# Marker the runner stats to decide headless (default) vs real WebKit WebView.
WEBVIEW_MARKER="$RUNNER_INSTALL_DIR/webview.enabled"
# The helper opens this audit log at startup; /var/lib is writable.
HELPER_STATE_DIR="/var/lib/gatepath"
# The helper writes its generated wpa_supplicant conf here (connectivity.rs).
HELPER_RUNTIME_DIR="/run/gatepath"

# ── D-Bus integration contract (verified against the helper source) ──────
DBUS_NAME="cc.grepon.Gatepath.NetNsHelper"
DBUS_OBJ="/cc/grepon/Gatepath/NetNsHelper"
DBUS_IFACE="cc.grepon.Gatepath.NetNsHelper1"

# Where the runner drops its no-leak verdict (host /tmp; the WebView transient
# unit does NOT set PrivateTmp, so this is visible to run.sh as root).
RUNNER_VERDICT="/tmp/gatepath-hwsim-runner.json"

# ── Logging ──────────────────────────────────────────────────────────────
if [ -t 1 ]; then
  _c_reset=$'\033[0m'; _c_ok=$'\033[32m'; _c_no=$'\033[31m'
  _c_warn=$'\033[33m'; _c_hdr=$'\033[1;36m'; _c_dim=$'\033[2m'
else
  _c_reset=""; _c_ok=""; _c_no=""; _c_warn=""; _c_hdr=""; _c_dim=""
fi

hdr()  { printf '\n%s== %s ==%s\n' "$_c_hdr" "$1" "$_c_reset"; }
log()  { printf '%s[hwsim]%s %s\n' "$_c_dim" "$_c_reset" "$1"; }
ok()   { printf '  %sOK%s   %s\n'   "$_c_ok"   "$_c_reset" "$1"; }
warn() { printf '  %sWARN%s %s\n'   "$_c_warn" "$_c_reset" "$1" >&2; }
err()  { printf '  %sFAIL%s %s\n'   "$_c_no"   "$_c_reset" "$1" >&2; }

# die: print an error and exit non-zero. run.sh's EXIT trap still fires, so
# cleanup runs. We deliberately do NOT use `set -e` anywhere — every failure
# is handled explicitly so teardown is unconditional.
die() { err "$1"; exit "${2:-1}"; }

have() { command -v "$1" >/dev/null 2>&1; }
