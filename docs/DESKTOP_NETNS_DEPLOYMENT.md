# Desktop netns isolation — feasibility, blockers, and atomic-distro deployment

This document records the findings from evaluating whether Gatepath desktop can
deliver Android-grade captive-portal isolation on an **immutable/atomic**
Fedora distribution (the concrete target is **Bazzite**), and how the
privileged network-namespace ("netns") helper should be deployed there.

It is a findings/design doc, not an implementation. Where it identifies work
that must happen before the isolated path functions on real hardware, those
items are tracked in [`BLOCKERS.md`](BLOCKERS.md).

---

## 1. The mental model: netns is the single-host DisposableVM

The goal is the one you already get in Qubes OS: a **throwaway, isolated
compartment** that talks to the captive portal over the raw Wi-Fi link,
completes sign-in, and is then destroyed — so the rest of your stack keeps
running behind its VPN and encrypted DNS, and nothing leaks to the portal
operator.

Gatepath desktop implements that compartment as a **network namespace** rather
than a VM:

```
create netns "gatepath"
   → move the Wi-Fi interface into it
      → run the portal WebView *inside* the netns
         → user signs in (or 10-min timeout)
            → tear the netns down
               → you are back on the trusted/VPN host stack
```

Every socket the portal browser opens is kernel-confined to that namespace.
The VPN tunnel, encrypted DNS, and normal browsing live in the **host** netns
and are never visible to the portal. This is the direct analogue of Android's
`ConnectivityManager.bindProcessToNetwork()` (kernel-enforced binding), and the
same *shape* as a Qubes `netvm` + DisposableVM split — just at namespace weight
instead of VM weight.

The architecture for this already exists in the tree:

- `desktop/gatepath-netns-helper/` — privileged Rust D-Bus daemon
  (`SetupCaptive` → `LaunchPortal` → `TeardownCaptive`).
- `desktop/gatepath/netns_client.py`, `desktop_isolation.py` — the
  unprivileged orchestrator that drives it.
- `desktop/gatepath/window.py`, `app.py` — GTK wiring; falls back to the
  in-process WebView when the helper is absent (the Flatpak-only path).

The unit/JVM-style tests pass (109 Rust, 233 Python), but they exercise the
privileged kernel operations through **fakes** (`FakeNetnsOps`, `FakeSpawner`).
That is why the bugs in §3 below have not surfaced.

---

## 2. Privilege reality: there is no fully unprivileged path to the guarantee

Moving the **physical Wi-Fi device** into *any* compartment — a netns, a
container, or a VM — requires `CAP_NET_ADMIN` in the **host (init) network
namespace**.

An *unprivileged user namespace* (`unshare --user --net`, rootless podman,
etc.) makes you "root" only **inside** the new namespace. It grants no
authority over host hardware, so it cannot move `wlan0` out of the host stack.
This is by kernel design and is not a packaging problem.

Qubes does not escape this either — it relocates the privilege into the
hypervisor/dom0, which PCI-passes the NIC to a `netvm`. On a single Linux host,
the equivalent of "dom0 owns the NIC" is **a small root helper that owns the
`CAP_NET_ADMIN` operation** — which is exactly what `gatepath-netns-helper` is.

There is a *best-effort* unprivileged fallback (a rootless user+net namespace
plus a userspace network stack such as `pasta`/`passt` or `slirp4netns`, pinned
to the Wi-Fi interface's **source address**). It can route on-link captive
traffic out of Wi-Fi without root, but it **cannot promise "no leakage"**: DNS
and any off-link portal traffic can still follow the VPN's default route.
Because no-leak is the entire point, this fallback does not satisfy the
requirement and is documented here only to record that it was considered and
rejected as the primary mechanism.

**Conclusion:** the privileged helper is unavoidable for the kernel-enforced
guarantee. The question is therefore *how to deploy that helper on an immutable
`/usr`*, not *whether to avoid it*.

---

## 3. Open blockers found during this evaluation

These must be resolved before the isolated path works on real hardware — on
**any** distro, atomic or not. Both are tracked in [`BLOCKERS.md`](BLOCKERS.md).

### 3a. The Wi-Fi interface is moved with the wrong kernel operation

`desktop/gatepath-netns-helper/src/netns.rs` moves the interface with:

```rust
ip link set dev <interface> netns <netns_name>
```

This does **not** work for Wi-Fi. A wireless netdev is bound to its `wiphy`
(the PHY); the kernel refuses to move the netdev alone and returns
`-EOPNOTSUPP` / "Invalid argument". The wireless stack requires moving the
**whole PHY**:

```
iw phy <phyN> set netns name <netns_name>     # or: set netns <pid>
```

So the central "straight to the Wi-Fi" step fails today. The validator
(`validation.rs`) and the orchestration around it are fine; the single
privileged op is wrong for the one device class Gatepath targets.

### 3b. Nothing re-establishes connectivity inside the netns

Even once the PHY is moved correctly, the new netns has **no link**:

- NetworkManager lives in the **host** netns and can no longer see or manage
  the moved PHY.
- Moving a connected wiphy drops the L2 association on most drivers, and the
  DHCP lease does not travel with it.

So the helper must also, inside the gatepath netns, run its own
`wpa_supplicant` (re-associate to the captive SSID) **and** a DHCP client
(reacquire an address + the gateway/portal route) before the WebView runner is
spawned. None of that exists yet. This is the larger of the two items.

> Implication for the parity table: until 3a **and** 3b land, desktop "bind
> portal traffic to the Wi-Fi interface" is **architected but non-functional**,
> not "done". The README and SECURITY_MODEL have been corrected to say so.

---

## 4. Deploying the helper on Bazzite (immutable `/usr`)

The helper's files are hardcoded to FHS paths under the read-only `/usr` tree:

| File | Hardcoded path |
|------|----------------|
| helper binary | `/usr/libexec/gatepath-netns-helper` (systemd `ExecStart`) |
| portal runner wrapper | `/usr/lib/gatepath/portal-webview-runner` (`spawn.rs:PORTAL_RUNNER_PATH`) |
| systemd unit | `/usr/lib/systemd/system/gatepath-netns-helper.service` |
| D-Bus system-service | `/usr/share/dbus-1/system-services/…` |
| D-Bus policy | `/usr/share/dbus-1/system.d/…` (or `/etc/dbus-1/system.d/…`) |
| polkit action | `/usr/share/polkit-1/actions/…` |

A second, easy-to-miss constraint: the portal runner is spawned **on the host**
(netns'd, dropped to your UID) and runs `python3 -m gatepath.portal_webview_runner`.
So the `gatepath` Python package **and** host GTK4/libadwaita/WebKitGTK +
PyGObject must be present on the host — they are not reachable from inside the
Flatpak sandbox. Any deployment option below must account for that too.

There are three realistic ways to get these onto Bazzite. Flatpak and Distrobox
are **not** among them: both are sandboxed/containerized and cannot move the
host NIC or own a host system-D-Bus name.

### Option A — Layered RPM (`rpm-ostree install`)

Build an RPM that installs every file to its canonical `/usr` path and depends
on `python3-gobject`, `webkit2gtk` (or `webkitgtk6.0`), and `iproute2`/`iw`.

**Pros**
- Canonical, conventional packaging; files land exactly where the code expects,
  so **no source changes** are needed.
- polkit/D-Bus/systemd discovery all "just work" (standard `/usr/share` paths).
- Clean install/remove/upgrade with familiar RPM tooling; can be signed.
- Best understood by anyone who later packages for plain Fedora/RHEL.

**Cons**
- Layering adds to **every** `rpm-ostree upgrade` and slows rebases; it is the
  approach uBlue/Bazzite explicitly discourage for general software.
- Requires a **reboot** to apply or remove.
- Can break or block updates across major Fedora bumps if a dependency moves.

### Option B — systemd system extension (`systemd-sysext`)

Ship a sysext image (a squashfs/dir under `/var/lib/extensions/`) that overlays
`/usr` at runtime, toggled with `systemd-sysext merge` / `unmerge`.

**Pros**
- Purpose-built for "add files to an immutable `/usr`" — and because sysext
  overlays `/usr`, the hardcoded paths work **unchanged, no source edits**.
- **No rpm-ostree layering**, so zero rebase friction; survives OS updates.
- Reversible at runtime (`unmerge`) and inspectable; no reboot strictly needed
  (a `systemd-sysext.service` re-merges on boot).
- With `extension-release` `ID=_any` (systemd ≥ v252, which Bazzite has), the
  image loads regardless of OS version — little/no regeneration on bumps.

**Cons**
- Less familiar; fewer users/operators know how to debug it.
- sysext covers `/usr` (+`/opt`) only, **not `/etc`** — fine here because all
  our files live under `/usr`, but it is a sharp edge to remember.
- `ID=_any` trades the OS-version safety check for convenience; you must
  re-verify the binary still runs after a major platform jump yourself.
- You build/maintain the image yourself (no distro package signing story).

### Option C — Writable-path installer (`/etc` + `/usr/local`)

An `install.sh` that drops the binary in `/usr/local/lib/gatepath/`, the unit
in `/etc/systemd/system/`, the D-Bus policy in `/etc/dbus-1/system.d/`, the
activation file in `/usr/local/share/dbus-1/system-services/`, and — because
polkit reads **actions** only from read-only `/usr/share` — a polkit
**rules.d** JS rule in `/etc/polkit-1/rules.d/` instead of a `.policy` action.

**Pros**
- Works **immediately**, no reboot, no rpm-ostree/sysext machinery.
- Easiest to iterate on during the 3a/3b development above.

**Cons**
- Requires a small packaging/source tweak: the systemd `ExecStart` and
  `spawn.rs:PORTAL_RUNNER_PATH` must point at the `/usr/local` locations.
- Uses a polkit `rules.d` workaround rather than a declared action (no
  description string / admin-keep defaults from a `.policy`).
- Least conventional; most prone to drifting out of sync or being clobbered;
  worst discoverability for anyone else.

### Recommendation

For a security daemon that holds `CAP_NET_ADMIN` and moves your NIC, **prefer
Option B (systemd-sysext) as the primary path on Bazzite**, with **Option A
(layered RPM) as the close, more-conventional alternative**:

- Choose **sysext** if your priority is *avoiding rpm-ostree layering and
  rebase friction* — a very Bazzite-aligned priority. It keeps the hardcoded
  `/usr` paths working with no source changes, is reversible at runtime, and
  with `ID=_any` needs little maintenance across OS updates. This is the best
  day-to-day fit for an atomic host.
- Choose the **RPM** if your priority is *conventionality and a signed,
  cleanly-upgradable package* and you accept layering friction + a reboot. It
  is the right artifact to publish if Gatepath is ever packaged for plain
  Fedora, so the work is reusable.

Use **Option C only for development iteration** while 3a/3b are being built —
not as the shipping mechanism.

Either way, the deployment work is **gated on the §3 blockers**: there is no
point shipping a polished installer for a netns whose Wi-Fi move does not yet
function. Build the wiphy move + in-netns supplicant/DHCP first, validate on
real hardware, then package with sysext (or RPM).

---

## 5. Summary

- The netns design is the correct single-host analogue of your Qubes
  DisposableVM model, and it can run on Bazzite.
- A privileged helper is **unavoidable** for a no-leak guarantee; there is no
  fully unprivileged path (Qubes doesn't have one either — it just moves the
  privilege into dom0).
- The blocker is not the atomic distro. It is that the helper (a) moves Wi-Fi
  with the wrong kernel op and (b) never re-establishes connectivity inside the
  netns. Both are tracked in [`BLOCKERS.md`](BLOCKERS.md).
- For packaging, **systemd-sysext** is the recommended primary on Bazzite, with
  a **layered RPM** as the conventional alternative; a writable-path installer
  is for dev only.
</content>
</invoke>
