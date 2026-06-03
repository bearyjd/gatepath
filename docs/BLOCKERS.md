# Gatepath — Build Blockers

This file tracks issues that block parts of the MVP from being verified end-to-end in
the build environment. Each entry should name the file, the symptom, the diagnosis, and
the workaround (if any).

## Status

The two code-level blockers that gated the desktop **netns isolation** path
(the Android-parity "bind portal traffic to the Wi-Fi interface" capability)
are now **implemented** — see RESOLVED entries below. One open item remains:
real-hardware validation of the privileged exec paths (the unit suite still
exercises them only through fakes), tracked as **BLOCKER-DESK-003**, plus the
documented open-networks-only limitation. See
[`DESKTOP_NETNS_DEPLOYMENT.md`](DESKTOP_NETNS_DEPLOYMENT.md) for the full
findings and the atomic-distro deployment analysis.

---

## Open

### BLOCKER-DESK-003 — Privileged exec paths are not yet hardware-validated

**Files:** `desktop/gatepath-netns-helper/src/netns.rs`
(`LinuxNetnsOps::move_interface` → `iw`), `src/connectivity.rs`
(`LinuxNetnsConnectivity` → `wpa_supplicant` + DHCP client).

**Symptom:** None observable in CI. The DESK-001/002 fixes are covered by
unit tests at the **command-construction** and **orchestration** level
(`phy_set_netns_args`, `render_wpa_config`, the `*_args` builders, and
service-level setup/teardown tests via `FakeNetnsConnectivity`), but the
actual privileged execution — moving a real PHY, re-associating with
`wpa_supplicant`, and pulling a DHCP lease inside the netns — has never run
against real Wi-Fi hardware in this environment (`iw`/`wpa_supplicant`/DHCP
clients aren't installed on the build host).

**Diagnosis:** This is the same fakes-hide-the-kernel gap that the original
DESK-001/002 entries called out, now narrowed to "the code is correct by
construction and review, but unverified end-to-end on hardware." The
`tests/dbus_integration.rs` `--ignored` suite is the intended home for
on-hardware checks.

**Workaround:** Validate on a real (non-Flatpak) Linux box with an **open**
captive SSID:

```
cd desktop/gatepath-netns-helper
cargo test --test dbus_integration -- --ignored --nocapture   # wire-shape checks
# then a manual end-to-end run of SetupCaptive → LaunchPortal → TeardownCaptive
```

Confirm: the PHY appears inside `ip netns exec gatepath iw dev`, the interface
gets an IPv4 address, the portal loads, and teardown removes the netns and
leaves no `wpa_supplicant`/DHCP strays (`ip netns pids gatepath`).

**Hardware-validation checklist** (items the unit suite cannot cover; several
surfaced in security/code review):

- [ ] `iw phy <phyN> set netns name gatepath` is accepted by the deployed `iw`
      version (older `iw` may only support `set netns <pid>`).
- [ ] The wireless netdev keeps its name after the PHY move (no udev rename
      inside the bare netns); otherwise `link_up_args` / DHCP target the wrong
      iface.
- [ ] DHCP actually completes (the one-shot client exits 0) on a real open
      captive AP, and `bring_up` returns only after a lease.
- [ ] systemd hardening is compatible with the in-netns children: `AF_PACKET`
      present and `IPAddressDeny` not blocking DHCP for wpa_supplicant + the
      DHCP client (which share the helper's unit/cgroup), with the helper proper
      now under `MemoryDenyWriteExecute=true` (verify end-to-end in
      `data/gatepath-netns-helper.service`).
- [ ] **DESK-003 C4 transient WebView unit** (`src/spawn.rs` `systemd_run_args`):
      `systemd-run` joins the netns via `NetworkNamespacePath=/var/run/netns/gatepath`,
      drops to the caller via `--uid`/`--gid`, and the WebKit JIT runs under that
      unit's `MemoryDenyWriteExecute=no` while the helper keeps W^X. Also confirm
      the WebView gets the user's graphical-session env (`WAYLAND_DISPLAY`/`DISPLAY`,
      `XDG_RUNTIME_DIR`, `DBUS_SESSION_BUS_ADDRESS`) — not plumbed yet; today the
      transient unit inherits none, same display gap the prior fork path had.
- [ ] Teardown leaves no stray privileged processes; the SIGTERM→SIGKILL
      straggler sweep reaps the DHCP client and supplicant before `ip netns del`.
      The transient WebView unit is `--collect`-cleaned by systemd; confirm no
      orphan `run-*.service` survives a teardown.
- [x] `cargo audit` runs in CI — **done**: a `cargo audit` job gates the
      desktop workflow against the RustSec advisory DB, and `Cargo.lock` is now
      committed for determinism.
- [x] **Detect the captive network's security from NetworkManager** —
      **done**: `active_network_is_open` reads the AP `flags`/`wpa_flags`/
      `rsn_flags`, and setup refuses a secured network with
      `RefusalReason::UnsupportedSecurity` **before** the PHY move (no more
      tearing away the user's Wi-Fi only to fail at DHCP).
- [x] **Move connectivity teardown out from under the `active` lock** —
      **done**: all three teardown paths now take the session out under the
      lock, release it, and stop wpa_supplicant/DHCP lock-free before
      `destroy_netns` (each path keeps its own error-clearing semantics).
- [x] **Restore `MemoryDenyWriteExecute` on the helper proper** —
      **implemented (DESK-003 C4), end-to-end pending hardware.** The unit now
      sets `MemoryDenyWriteExecute=true`, so the long-lived root helper keeps
      W^X. The only process that needs W+X (the WebKitGTK JIT) is launched in
      its **own transient systemd `.service`** via `systemd-run` with
      `MemoryDenyWriteExecute=no` on that unit alone (`src/spawn.rs`,
      `systemd_run_args`). Because the transient unit is forked by PID 1, it does
      not inherit the helper's (non-removable) W^X seccomp filter; wpa_supplicant
      and the DHCP client don't JIT, so they run fine under it. The argv shape is
      pinned by unit tests; the **exec** — `systemd-run` joining the netns via
      `NetworkNamespacePath=`, dropping privilege via `--uid`/`--gid`, and the
      JIT actually running under `MemoryDenyWriteExecute=no` — is unverified off
      hardware and stays on the validation list below.

**Known limitation — non-UTF-8 SSIDs:** `active_ssid` returns a lossy UTF-8
`String`, so an SSID with non-UTF-8 bytes is hex-encoded from the lossy form and
won't match the real beacon. Plumb raw `Vec<u8>` end-to-end if a real captive
network with a non-UTF-8 SSID is encountered (rare).

### Known limitation — secured captive networks are not supported

`connectivity.rs` re-associates **open** SSIDs only (`key_mgmt=NONE`), which is
the overwhelming captive-portal case. Secured captive networks (WPA2-PSK or
enterprise EAP) need the PSK/credentials lifted out of NetworkManager's secret
store before `wpa_supplicant` can re-associate inside the netns — a separate,
security-sensitive piece of work. `WifiSecurity::Psk` is modelled but
`bring_up` returns `ConnectivityError::Unsupported` rather than silently
producing a session that can never associate.

---

## Resolved

### BLOCKER-DESK-001 (RESOLVED 2026-06-01) — Wi-Fi PHY now moved with `iw`

**File:** `desktop/gatepath-netns-helper/src/netns.rs` (`LinuxNetnsOps::move_interface`)

`move_interface` no longer uses the netdev-only `ip link set dev <iface> netns`
form the wireless stack rejects with `-EOPNOTSUPP`. It now resolves the owning
PHY for the validated interface from sysfs
(`/sys/class/net/<iface>/phy80211/name`) and moves the **whole PHY** with
`iw phy <phyN> set netns name <name>` (the `nl80211`
`NL80211_CMD_SET_WIPHY_NETNS` operation). The interface name's restricted
charset (`[A-Za-z0-9_-]`, enforced by `validation`) keeps the sysfs lookup
injection-safe. The exact `iw` argv is pinned by `phy_move_uses_iw_whole_phy_form_not_ip_link`
so it can't regress. End-to-end exec is tracked by BLOCKER-DESK-003.

### BLOCKER-DESK-002 (RESOLVED 2026-06-01) — connectivity re-established in the netns

**Files:** `desktop/gatepath-netns-helper/src/connectivity.rs` (new),
`src/service.rs` (setup/teardown wiring), `src/network_manager.rs`
(`active_ssid`).

The new `connectivity` module brings the link up, runs `wpa_supplicant` to
re-associate to the captive SSID, and runs a DHCP client — all inside the
gatepath netns, behind the `NetnsConnectivity` trait. The SSID is captured from
NetworkManager **before** the PHY moves (after the move NM can't see the
device). The live session is stored by the orchestrator and dropped — which
stops `wpa_supplicant`/DHCP — **before** `destroy_netns` on every teardown path
(explicit, sender-disconnect, backstop). This adds `iw`, `wpa_supplicant`, and a
DHCP client to the helper's runtime dependency set (see
[`DESKTOP_NETNS_DEPLOYMENT.md`](DESKTOP_NETNS_DEPLOYMENT.md)). Open-networks-only
and on-hardware validation remain (see Open, above).

---

## Resolved (earlier)

### KNOWN-AND-001 (RESOLVED 2026-05-17) — `BindWatchdog` lifecycle delivery is JVM-tested

`BindWatchdog` was extracted from `GatepathApplication.kt` into its own
file (`android/app/src/main/java/cc/grepon/gatepath/BindWatchdog.kt`) so
the JVM runner can compile it without the Android `Application` + Hilt
deps. `run-jvm-tests.sh` now downloads
`androidx.lifecycle:lifecycle-common-jvm:2.8.7`,
`lifecycle-runtime-jvm:2.8.7`, and the transitive
`androidx.arch.core:core-common:2.2.0` from
`https://dl.google.com/dl/android/maven2/`.

`BindWatchdogTest.kt` walks a `LifecycleRegistry` (constructed via
`createUnsafe` to skip the main-thread assertion) through the documented
scenarios:

- `ON_RESUME → ON_PAUSE → ON_RESUME` (no `ON_STOP`) — lambda must NOT fire.
- Full cycle ending in `ON_STOP` — lambda must fire exactly once.
- Three back-to-back foreground/background cycles — lambda fires once per
  `ON_STOP`.

All three pass via `./gradlew :app:testDebugUnitTest`.

---

### BLOCKER-AND-001 (RESOLVED 2026-05-05) — `kotlinc` was not on PATH

The Android JVM unit tests are written against pure-Kotlin business logic and require
`kotlinc` (not the Android SDK) to compile. They are now executed by
`android/run-jvm-tests.sh`, which downloads kotlinc-2.0.21 if it is not on PATH (CI) and
wires the bundled `kotlinx-serialization-compiler-plugin.jar` so `@Serializable` data
classes generate their serializers at compile time.

**Result:** 35/35 JVM unit tests pass locally
(`PortalProbeTest`, `SessionStateTest`, `AuditLogTest`, `BlockedDomainsTest`).

**Still requires the Android SDK:** `./gradlew :app:assembleDebug` produces the APK.
That step is run in CI (`.github/workflows/android.yml`), not locally.
