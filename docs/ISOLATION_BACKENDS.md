# Isolation backends — design alternatives (ADR)

**Status:** Accepted (netns is the implemented backend; VM-passthrough is the
documented stronger alternative, not yet built).
**Context doc:** [`RATIONALE.md`](RATIONALE.md) (why Gatepath exists) and
[`DESKTOP_NETNS_DEPLOYMENT.md`](DESKTOP_NETNS_DEPLOYMENT.md) (deploying the
helper, blockers).

This record captures *how* Gatepath builds the disposable compartment that owns
the Wi-Fi during captive sign-in, the three primitives considered, and why we
land where we do. It exists because "use a container" and "use a VM" are both
reasonable-sounding and only one of them is actually different from what we
already have.

The job, restated: **give exactly one process (the portal browser) direct
access to the hostile Wi-Fi, keep everything else behind the VPN/kill-switch,
then destroy the compartment.** See [`RATIONALE.md`](RATIONALE.md) §4 for why
this is the right shape and why, at sign-in, borrowing the radio costs nothing.

---

## Option A — Network namespace (implemented)

Move the Wi-Fi PHY into a dedicated netns, run the WebView there, tear it down.

- **Network isolation:** kernel-enforced, bidirectional.
- **Privileged surface added:** *small* — the `gatepath-netns-helper` daemon,
  whose authority is narrowly "move the captive interface / tear it down."
- **Cost per session:** milliseconds; native host window for the WebView.
- **Contains a browser RCE?** **No.** The browser shares the host kernel. netns
  confines *where packets go*, not *whether rendering hostile HTML is safe*. A
  WebKit bug that targets the kernel still lands on the host.
- **Status:** the Wi-Fi PHY move (`iw phy … set netns`, not `ip link`) and
  in-netns association/DHCP re-establishment (`wpa_supplicant` + DHCP) are both
  implemented (BLOCKER-DESK-001/002 resolved) and **validated end-to-end on a
  `mac80211_hwsim` virtual radio** (BLOCKER-DESK-003 resolved). Physical-card
  confirmation and secured-network support remain — see
  [`BLOCKERS.md`](BLOCKERS.md).

**Verdict:** the pragmatic, lightweight backend. Smallest TCB, works on any
machine (no hardware requirement), but the weakest containment of the *actual*
threat (hostile content in a shared-kernel browser).

## Option B — Container (Docker / Podman) — **rejected**

A container's network isolation **is** a network namespace. Giving a container
the physical Wi-Fi is the *same* `iw phy set netns` move into the container's
netns, with the *same* `CAP_NET_ADMIN` requirement and the *same*
association/DHCP problem. So a container adds **nothing** on the axis that
matters here.

What it *does* add — mount/PID/seccomp confinement of the browser process — is
real but (a) still shares the host kernel, so it doesn't stop the kernel-targeting
escape we care about, and (b) is obtainable far more cheaply with `bubblewrap` +
a seccomp profile around the existing runner, with no container runtime. Docker
specifically also drags in a large root-privileged daemon — *more* attack
surface for *no* stronger network boundary.

**Verdict:** worst of the three for this job. If we want cheap fs/seccomp
hardening of the runner, do it with bubblewrap on the netns path, not a
container.

## Option C — VM + Wi-Fi passthrough (the rigorous / Qubes-faithful option)

A QEMU/KVM (or microVM) guest that **owns the Wi-Fi NIC via VFIO passthrough**,
runs its own kernel + NetworkManager + browser, signs in, and is destroyed.
This is exactly what Qubes does — and *why* Qubes uses VMs over containers: it
distrusts shared-kernel isolation for hostile content.

- **Network isolation:** kernel-enforced (the guest's own kernel).
- **Contains a browser RCE?** **Yes** — a WebKit RCE *and* a guest-kernel
  privesc still only yield a throwaway guest with access to the hostile network
  it already came from. Only a *hypervisor* escape reaches the host, a much
  smaller/harder surface.
- **In-guest networking is easy:** the guest sees an ordinary Wi-Fi card and
  runs stock association + DHCP. This **deletes BLOCKER-DESK-001/002** — the
  hard part moves from "per-driver netns dance" to "pass the device through
  once."
- **Privileged surface added:** *large* — qemu/libvirt + the VFIO device bind.
  Bigger orchestration TCB than the netns helper, traded for much better
  containment of the threat.
- **Cost per session:** seconds (full VM) or ~1s (microVM: cloud-hypervisor or
  QEMU `microvm` + VFIO; Firecracker is out — no arbitrary PCI passthrough).
- **GUI plumbing:** the browser runs in the guest, so it needs SPICE/VNC or a
  kiosk-framebuffer surface — more UX work than the native netns window.
- **Hardware requirement:** IOMMU (VT-d/AMD-Vi) enabled, and a Wi-Fi device
  that **isolates cleanly in its own IOMMU group**. This is the make-or-break.

### Hardware feasibility — the deciding factor

PCI passthrough's usual nemesis is laptops, where the built-in Wi-Fi shares an
IOMMU group with bridges/other functions and won't isolate without ACS-override
patches (which themselves weaken isolation). Two things make Gatepath's target
case better than average:

- The stated target is **Bazzite Tower** — a desktop, far more likely to have a
  discrete PCIe Wi-Fi card in its own IOMMU group.
- A **USB Wi-Fi dongle** passes through trivially (USB passthrough by
  `vendor:product` is much simpler than PCI/VFIO), giving a guaranteed-clean
  path even if the built-in card's group is dirty.

Run [`desktop/tools/check-vfio-feasibility.sh`](../desktop/tools/check-vfio-feasibility.sh)
on the box to get a concrete verdict (IOMMU on/off, the Wi-Fi device, its IOMMU
group membership, PCI-vs-USB, current driver). Interpretation is in §"Reading
the checker output" below.

**Verdict:** the philosophically correct backend if the goal is truly
Qubes-grade isolation, and the one that earns that label honestly. Gated on
hardware feasibility (likely-favorable on a Tower / with a USB dongle) and a
larger orchestration effort.

---

## Decision

- **Ship the netns backend** as the baseline (lightweight, no hardware
  requirement) — but be explicit in the docs that it does **not** contain a
  browser RCE (shared kernel).
- **Document the VM-passthrough backend as the stronger, opt-in tier** for
  hardware that supports clean passthrough. It is the right answer for the
  Qubes-grade threat model and, as a bonus, removes the two netns blockers by
  moving Wi-Fi setup inside a normal guest.
- **Reject containers/Docker** as a backend — same network primitive as netns,
  larger surface, no stronger boundary. Use `bubblewrap`+seccomp on the netns
  path if/when we want cheap process-level hardening of the runner.

A future implementation may offer both backends behind one orchestrator: netns
where passthrough isn't available, VM where it is.

---

## Reading the checker output

`check-vfio-feasibility.sh` is read-only (it touches nothing, changes nothing)
and prints a verdict per Wi-Fi device:

- **IOMMU: enabled** — required for any PCI passthrough. If disabled, enable
  `intel_iommu=on` (Intel) or `amd_iommu=on` + `iommu=pt` (AMD) on the kernel
  cmdline first. On Bazzite that's an `rpm-ostree kargs` change + reboot.
- **USB Wi-Fi** — verdict is **FAVORABLE** regardless of IOMMU groups: USB
  passthrough is by device, not IOMMU group. The simplest reliable VM path.
- **PCI Wi-Fi, group contains only the Wi-Fi function(s)** — verdict
  **CLEAN**: passthrough should work without ACS hacks.
- **PCI Wi-Fi, group contains unrelated devices** — verdict **DIRTY**: clean
  VFIO passthrough isn't possible without moving the card, an ACS-override
  kernel patch (weakens isolation — not recommended for a security tool), or
  switching to a USB dongle.

A DIRTY built-in card doesn't kill the VM design — it just means "use a USB
Wi-Fi dongle for the captive compartment," which is arguably *cleaner* anyway
(the disposable compartment owns a disposable, dedicated radio).
