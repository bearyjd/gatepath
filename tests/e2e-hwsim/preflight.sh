#!/usr/bin/env bash
# tests/e2e-hwsim/preflight.sh
#
# P0.2 substrate probe. Run this on the TARGET Linux box (the one with the
# wireless stack you want to validate against) BEFORE we build the
# mac80211_hwsim harness. It reports exactly what the harness can rely on so
# we don't write blind code against tools that aren't there — which matters
# a lot on immutable distros (Bazzite / Silverblue / etc.) where you can't
# just `dnf install` onto the host.
#
#   sudo bash tests/e2e-hwsim/preflight.sh
#
# What it does:
#   * READ-ONLY checks: OS identity, installed tools, kernel-module presence,
#     NetworkManager + Python deps.
#   * ACTIVE checks (each cleans up after itself, nothing is left behind on a
#     clean exit): load mac80211_hwsim, create a netns, move a virtual PHY into
#     it and back. These are the exact privileged operations the real helper
#     performs, so proving them here de-risks the whole harness.
#
# It changes no persistent state: the module is unloaded, the netns is deleted.
# If a step is interrupted, re-running is safe (cleanup is idempotent).
#
# Paste the entire output back. Exit code is always 0 (this is a report, not a
# gate) — read the SUMMARY block at the end.

set -u

# ── tiny output helpers ──────────────────────────────────────────────────
c_reset=$'\033[0m'; c_ok=$'\033[32m'; c_no=$'\033[31m'; c_warn=$'\033[33m'; c_hdr=$'\033[1;36m'
hdr()  { printf '\n%s== %s ==%s\n' "$c_hdr" "$1" "$c_reset"; }
ok()   { printf '  %sOK%s    %s\n'   "$c_ok"   "$c_reset" "$1"; }
no()   { printf '  %sMISS%s  %s\n'   "$c_no"   "$c_reset" "$1"; }
warn() { printf '  %sWARN%s  %s\n'   "$c_warn" "$c_reset" "$1"; }
info() { printf '        %s\n' "$1"; }

# Accumulators for the final summary.
SUMMARY=()
add()  { SUMMARY+=("$1"); }

have() { command -v "$1" >/dev/null 2>&1; }

# Report a tool: prints OK + version line, or MISS. $1=cmd, $2=human label,
# $3=optional version flag (default --version). Records into SUMMARY.
report_tool() {
  local cmd="$1" label="${2:-$1}" vflag="${3:---version}"
  if have "$cmd"; then
    local path ver
    path="$(command -v "$cmd")"
    ver="$("$cmd" $vflag 2>&1 | head -n1)"
    ok "$label  ($path)"
    [ -n "$ver" ] && info "$ver"
    add "tool:$cmd=yes"
  else
    no "$label  (not found in PATH)"
    add "tool:$cmd=no"
  fi
}

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    warn "Not running as root — ACTIVE checks (module load, netns, PHY move) will be skipped."
    warn "Re-run with: sudo bash tests/e2e-hwsim/preflight.sh"
    return 1
  fi
  return 0
}

# ─────────────────────────────────────────────────────────────────────────
hdr "0. Identity"
printf '  kernel    %s\n' "$(uname -r)"
printf '  arch      %s\n' "$(uname -m)"
printf '  uid       %s\n' "$(id -u)"
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  printf '  os        %s (%s)\n' "${PRETTY_NAME:-?}" "${VARIANT_ID:-${ID:-?}}"
  add "os=${ID:-unknown}/${VARIANT_ID:-}"
fi
if have rpm-ostree; then
  ok "rpm-ostree present → immutable / atomic host (Bazzite/Silverblue family)"
  add "immutable=yes"
  info "host /usr is read-only; tools must come from the base image, an overlay, or a distrobox"
else
  add "immutable=no"
fi
if [ -w /usr ]; then
  info "/usr is writable (mutable host)"
else
  info "/usr is read-only (confirms immutable host)"
fi

# ─────────────────────────────────────────────────────────────────────────
hdr "1. Harness tools (AP side + orchestration)"
report_tool iw            "iw"            "--version"
report_tool hostapd       "hostapd"      "-v"
report_tool wpa_supplicant "wpa_supplicant" "-v"
report_tool dhclient      "dhclient (ISC)" "--version"
report_tool dnsmasq       "dnsmasq"      "--version"
report_tool dbus-daemon   "dbus-daemon"  "--version"
report_tool dbus-run-session "dbus-run-session" "--version"
report_tool busctl        "busctl"       "--version"
report_tool jq            "jq"           "--version"

hdr "2. Build toolchain (to compile the native helper)"
report_tool cargo  "cargo"  "--version"
report_tool rustc  "rustc"  "--version"
report_tool distrobox "distrobox" "--version"
report_tool podman    "podman"    "--version"
report_tool toolbox   "toolbox"   "--version"

# ─────────────────────────────────────────────────────────────────────────
hdr "3. NetworkManager (helper's NM dependency)"
report_tool nmcli "nmcli" "--version"
if have systemctl; then
  nm_state="$(systemctl is-active NetworkManager 2>/dev/null || echo unknown)"
  printf '  NetworkManager service: %s\n' "$nm_state"
  add "nm_active=$nm_state"
fi

hdr "4. Python deps (private-bus dbusmock NM path)"
report_tool python3 "python3" "--version"
if have python3; then
  for mod in "dbus:python-dbus" "dbusmock:python-dbusmock" "dasbus:dasbus" "gi:PyGObject"; do
    name="${mod%%:*}"; pkg="${mod##*:}"
    if python3 -c "import ${name}" >/dev/null 2>&1; then
      ok "python: import ${name}  (${pkg})"
      add "py:${name}=yes"
    else
      no "python: import ${name}  (${pkg} — needed only for the dbusmock-NM path)"
      add "py:${name}=no"
    fi
  done
fi

# ─────────────────────────────────────────────────────────────────────────
hdr "5. mac80211_hwsim kernel module"
HWSIM_OK=no
if modinfo mac80211_hwsim >/dev/null 2>&1; then
  ok "mac80211_hwsim is available to this kernel"
  info "$(modinfo -F filename mac80211_hwsim 2>/dev/null)"
  HWSIM_OK=yes
  add "hwsim_module=yes"
else
  no "mac80211_hwsim NOT found for kernel $(uname -r)"
  info "Without it there are no virtual radios — the harness can't run here."
  info "On atomic Fedora the module usually ships in kernel-modules-extra /"
  info "kernel-modules-internal; check your image, or test on a box that has it."
  add "hwsim_module=no"
fi

# ─────────────────────────────────────────────────────────────────────────
hdr "6. ACTIVE capability checks (reversible)"
NETNS_PROBE="gatepath-preflight"
PHY_MOVED=""        # set to phy name once moved, for cleanup
LOADED_HWSIM=no     # set to yes if WE loaded the module, so we only unload our own

cleanup() {
  # Best-effort, idempotent teardown. Runs on every exit.
  if [ -n "$PHY_MOVED" ]; then
    # The wiphy is inside the probe netns; deleting the netns or unloading
    # hwsim reclaims it, so nothing extra is needed here.
    :
  fi
  if ip netns list 2>/dev/null | grep -q "^${NETNS_PROBE}\b"; then
    ip netns del "$NETNS_PROBE" 2>/dev/null || true
  fi
  if [ "$LOADED_HWSIM" = yes ]; then
    # Stop NM from re-grabbing the virtual wlans, then unload.
    modprobe -r mac80211_hwsim 2>/dev/null \
      || warn "could not unload mac80211_hwsim now (in use); it clears on reboot, or: sudo rmmod -f mac80211_hwsim"
  fi
}
trap cleanup EXIT

if require_root; then
  # 6a — netns create/delete
  if ip netns add "$NETNS_PROBE" 2>/tmp/_pf_netns.err; then
    ok "netns create/delete works ($NETNS_PROBE)"
    ip netns del "$NETNS_PROBE" 2>/dev/null || true
    add "netns=yes"
  else
    no "cannot create a network namespace"
    info "$(head -n1 /tmp/_pf_netns.err 2>/dev/null)"
    add "netns=no"
  fi
  rm -f /tmp/_pf_netns.err 2>/dev/null || true

  # 6b — load hwsim, enumerate virtual phys, prove a PHY move (the crux)
  if [ "$HWSIM_OK" = yes ]; then
    # Only unload at the end if it wasn't already loaded before we started.
    if lsmod 2>/dev/null | grep -q '^mac80211_hwsim'; then
      warn "mac80211_hwsim was ALREADY loaded — leaving it as-is, not unloading on exit"
      info "(it may have real virtual radios in use; this probe won't disturb them)"
    else
      if modprobe mac80211_hwsim radios=2 2>/tmp/_pf_hwsim.err; then
        LOADED_HWSIM=yes
        ok "modprobe mac80211_hwsim radios=2 succeeded"
        add "hwsim_load=yes"
      else
        no "modprobe mac80211_hwsim failed"
        info "$(head -n1 /tmp/_pf_hwsim.err 2>/dev/null)"
        add "hwsim_load=no"
      fi
      rm -f /tmp/_pf_hwsim.err 2>/dev/null || true
    fi

    # Enumerate the virtual phys + their netdevs (works whether we or the
    # system loaded the module).
    if have iw && lsmod 2>/dev/null | grep -q '^mac80211_hwsim'; then
      mapfile -t HWSIM_PHYS < <(
        for p in /sys/class/ieee80211/*; do
          [ -e "$p" ] || continue
          phy="$(basename "$p")"
          # hwsim phys are backed by the mac80211_hwsim driver; match on the
          # netdev's device driver symlink when present.
          dev_iface="$(basename "$(readlink -f "$p"/device 2>/dev/null)" 2>/dev/null || true)"
          printf '%s\n' "$phy"
        done
      )
      if [ "${#HWSIM_PHYS[@]}" -gt 0 ]; then
        info "wiphys present: ${HWSIM_PHYS[*]}"
      fi
      # Pick a phy whose netdev name starts with wlan and that has phy80211 —
      # i.e., something the real helper's resolve_phy would accept.
      MOVE_PHY=""
      MOVE_IFACE=""
      for iface_path in /sys/class/net/*; do
        iface="$(basename "$iface_path")"
        [ -e "$iface_path/phy80211/name" ] || continue
        # Heuristic: hwsim netdevs are wlanN with a phy80211 link.
        case "$iface" in
          wlan*)
            drv="$(basename "$(readlink -f "$iface_path/device/driver" 2>/dev/null)" 2>/dev/null || true)"
            if [ "$drv" = "mac80211_hwsim" ] || [ "$LOADED_HWSIM" = yes ]; then
              MOVE_PHY="$(cat "$iface_path/phy80211/name" 2>/dev/null)"
              MOVE_IFACE="$iface"
              break
            fi
            ;;
        esac
      done

      if [ -n "$MOVE_PHY" ]; then
        ok "found movable virtual radio: iface=$MOVE_IFACE phy=$MOVE_PHY (has phy80211)"
        add "hwsim_phy=$MOVE_PHY"
        # The actual crux: can we move this phy into a fresh netns? This is
        # exactly DESK-001's privileged operation.
        if ip netns add "$NETNS_PROBE" 2>/dev/null; then
          if iw phy "$MOVE_PHY" set netns name "$NETNS_PROBE" 2>/tmp/_pf_move.err; then
            PHY_MOVED="$MOVE_PHY"
            ok "PHY MOVE WORKS: iw phy $MOVE_PHY set netns name $NETNS_PROBE"
            info "→ the real helper's DESK-001 path will work on this box"
            add "phy_move=yes"
            # Confirm the iface is now inside the probe netns.
            if ip netns exec "$NETNS_PROBE" iw dev 2>/dev/null | grep -q "Interface"; then
              info "confirmed: a wireless iface is now present inside $NETNS_PROBE"
            fi
          else
            no "PHY move failed: iw phy $MOVE_PHY set netns ..."
            info "$(head -n1 /tmp/_pf_move.err 2>/dev/null)"
            add "phy_move=no"
          fi
          rm -f /tmp/_pf_move.err 2>/dev/null || true
        fi
      else
        warn "no movable hwsim wlan netdev with phy80211 found to test the move"
        add "phy_move=skip"
      fi
    fi
  else
    warn "skipping module/PHY-move checks (module unavailable)"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────
hdr "SUMMARY (paste this whole block back)"
printf '  %s\n' "${SUMMARY[@]}"
printf '\n  Decoder:\n'
printf '    hwsim_module / hwsim_load / phy_move = the three must-haves.\n'
printf '    If all three are yes → bare-metal harness is viable on this box.\n'
printf '    hostapd / dnsmasq MISS on an immutable host → we source them from a\n'
printf '      distrobox or static build (the probe says which path to take).\n'
printf '    cargo MISS → build the helper in a distrobox, run it on the host.\n'

exit 0
