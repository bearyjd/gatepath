#!/usr/bin/env bash
#
# check-vfio-feasibility.sh — read-only diagnostic.
#
# Tells you whether this machine can pass a Wi-Fi NIC through to a VM for the
# Gatepath VM-passthrough isolation backend (see docs/ISOLATION_BACKENDS.md,
# Option C). It CHANGES NOTHING: it only reads /sys, /proc/cmdline, and runs
# `lspci`/`lsusb` if present. Safe to run as an unprivileged user (a few
# details are richer as root, but the verdict does not require it).
#
# For each wireless interface it reports:
#   - whether the IOMMU is enabled (required for PCI passthrough)
#   - the underlying device (PCI or USB) and its current kernel driver
#   - for PCI: the IOMMU group and every other device sharing that group
#   - a verdict: FAVORABLE (USB) / CLEAN (PCI, isolated) / DIRTY (PCI, shared)
#
# Exit code: 0 if at least one wifi device is FAVORABLE or CLEAN, 1 otherwise.

set -u

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
red() { printf '\033[31m%s\033[0m\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

best_verdict=1 # 1 = none found yet; set to 0 when a usable path exists

bold "== Gatepath VM-passthrough feasibility check =="
echo "(read-only; nothing on this system is modified)"
echo

# ── 1. IOMMU enabled? ────────────────────────────────────────────────────────
bold "1. IOMMU status"
iommu_on=0
if [ -d /sys/kernel/iommu_groups ] && [ -n "$(ls -A /sys/kernel/iommu_groups 2>/dev/null)" ]; then
    ngroups=$(find /sys/kernel/iommu_groups -maxdepth 1 -mindepth 1 -type d | wc -l)
    green "   IOMMU is ENABLED ($ngroups groups present)."
    iommu_on=1
else
    red "   IOMMU appears DISABLED (no populated /sys/kernel/iommu_groups)."
    echo "   PCI passthrough needs it. Kernel cmdline now:"
    echo "      $(cat /proc/cmdline 2>/dev/null)"
    echo "   Enable with intel_iommu=on (Intel) or amd_iommu=on iommu=pt (AMD)."
    echo "   On Bazzite/atomic:  rpm-ostree kargs --append=intel_iommu=on   (then reboot)"
    echo "   USB Wi-Fi passthrough does NOT require the IOMMU and still works."
fi
echo

# ── 2. Find wireless interfaces ──────────────────────────────────────────────
bold "2. Wireless interfaces"
mapfile -t wifis < <(
    for d in /sys/class/net/*; do
        [ -e "$d/wireless" ] || [ -e "$d/phy80211" ] || continue
        basename "$d"
    done
)
if [ "${#wifis[@]}" -eq 0 ]; then
    red "   No wireless interfaces found under /sys/class/net/."
    echo "   (A USB Wi-Fi dongle plugged in later would show up here and is the"
    echo "    easiest passthrough path regardless of this machine's IOMMU groups.)"
    exit 1
fi
printf '   Found: %s\n\n' "${wifis[*]}"

# ── 3. Per-interface analysis ────────────────────────────────────────────────
for iface in "${wifis[@]}"; do
    bold "── $iface ──"
    devlink="/sys/class/net/$iface/device"
    if [ ! -e "$devlink" ]; then
        yellow "   No backing device symlink; skipping."
        echo
        continue
    fi
    devpath=$(readlink -f "$devlink")
    driver="?"
    [ -e "$devlink/driver" ] && driver=$(basename "$(readlink -f "$devlink/driver")")

    # Bus type: USB if the resolved device path traverses a usb node.
    if printf '%s' "$devpath" | grep -q '/usb[0-9]'; then
        # ---- USB device ----
        green "   Bus: USB   Driver: $driver"
        idv=$(cat "$devlink/../idVendor" 2>/dev/null || cat "$devpath/../idVendor" 2>/dev/null || echo '????')
        idp=$(cat "$devlink/../idProduct" 2>/dev/null || cat "$devpath/../idProduct" 2>/dev/null || echo '????')
        echo "   USB ID (for qemu -device usb-host): ${idv}:${idp}"
        have lsusb && lsusb -d "${idv}:${idp}" 2>/dev/null | sed 's/^/   /'
        green "   VERDICT: FAVORABLE — USB passthrough is by device id, no IOMMU group needed."
        echo "            qemu:  -device usb-host,vendorid=0x${idv},productid=0x${idp}"
        best_verdict=0
    else
        # ---- PCI device ----
        pci=$(basename "$devpath")
        green "   Bus: PCI   Address: $pci   Driver: $driver"
        have lspci && lspci -nns "$pci" 2>/dev/null | sed 's/^/   /'
        grp_link="$devpath/iommu_group"
        if [ ! -e "$grp_link" ]; then
            if [ "$iommu_on" -eq 0 ]; then
                red "   VERDICT: BLOCKED — IOMMU is off, so no group info. Enable it (see §1) and re-run."
            else
                yellow "   VERDICT: UNKNOWN — no iommu_group for this device."
            fi
            echo
            continue
        fi
        grp=$(basename "$(readlink -f "$grp_link")")
        echo "   IOMMU group: $grp"
        mapfile -t members < <(
            for m in "$(readlink -f "$grp_link")"/devices/*; do
                basename "$m"
            done
        )
        # Count members that are NOT the wifi card itself and NOT a PCI bridge.
        foreign=0
        echo "   Group members:"
        for m in "${members[@]}"; do
            desc=""
            have lspci && desc=$(lspci -nns "$m" 2>/dev/null | sed "s/^$m //")
            tag=""
            if [ "$m" = "$pci" ]; then
                tag="  <- the Wi-Fi card"
            elif printf '%s' "$desc" | grep -qiE 'PCI bridge|Host bridge|Root Port'; then
                tag="  (bridge — usually OK)"
            else
                tag="  <- FOREIGN device in the group"
                foreign=$((foreign + 1))
            fi
            printf '      %s %s%s\n' "$m" "$desc" "$tag"
        done
        if [ "$foreign" -eq 0 ]; then
            green "   VERDICT: CLEAN — group has only the Wi-Fi card (+bridges). VFIO passthrough should work."
            [ "$best_verdict" -ne 0 ] && best_verdict=0
        else
            red "   VERDICT: DIRTY — $foreign unrelated device(s) share this group."
            echo "            Clean passthrough isn't possible without ACS-override (weakens"
            echo "            isolation — not recommended) or a different slot. Easiest fix:"
            echo "            use a USB Wi-Fi dongle for the captive compartment."
        fi
    fi
    echo
done

# ── 4. Summary ───────────────────────────────────────────────────────────────
bold "== Summary =="
if [ "$best_verdict" -eq 0 ]; then
    green "At least one Wi-Fi device can be passed through to a VM. The VM-passthrough"
    green "backend (docs/ISOLATION_BACKENDS.md, Option C) is feasible on this machine."
else
    yellow "No cleanly-passthrough-able Wi-Fi device found. Options: enable the IOMMU"
    yellow "(if it was off), move the card to an isolated slot, or — simplest — add a"
    yellow "USB Wi-Fi dongle dedicated to the captive compartment. The netns backend"
    yellow "(Option A) works regardless, with the caveats in the doc."
fi
exit "$best_verdict"
