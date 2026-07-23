# Gatepath — Build Blockers

This file tracks issues that block parts of the MVP from being verified end-to-end in
the build environment. Each entry should name the file, the symptom, the diagnosis, and
the workaround (if any).

## Status

The two code-level blockers that gated the desktop **netns isolation** path
(the Android-parity "bind portal traffic to the Wi-Fi interface" capability)
are now **implemented** — see RESOLVED entries below. The privileged exec paths
are now **validated end-to-end on a `mac80211_hwsim` virtual radio** by
`tests/e2e-hwsim/` — see BLOCKER-DESK-003 (RESOLVED) below. Remaining:
physical-card confirmation and a buildable package (tracked in
[`ROADMAP.md`](ROADMAP.md) P2.1), plus the documented open-networks-only
limitation. See [`DESKTOP_NETNS_DEPLOYMENT.md`](DESKTOP_NETNS_DEPLOYMENT.md)
for the full findings and the atomic-distro deployment analysis.

---

## Open

### Known limitation — secured captive networks are not supported

`connectivity.rs` re-associates **open** SSIDs only (`key_mgmt=NONE`), which is
the overwhelming captive-portal case. Secured captive networks (WPA2-PSK or
enterprise EAP) need the PSK/credentials lifted out of NetworkManager's secret
store before `wpa_supplicant` can re-associate inside the netns — a separate,
security-sensitive piece of work. `WifiSecurity::Psk` is modelled but
`bring_up` returns `ConnectivityError::Unsupported` rather than silently
producing a session that can never associate.

### Known limitation — desktop DoH-forwarder detection is intentionally not implemented

Desktop diagnostics detect strict systemd-resolved **DNS-over-TLS**
(`PrivateDnsBlocking`) by reading the `org.freedesktop.resolve1`
`Manager.DNSOverTLS` D-Bus property — cheap and reliable, and reachable inside
the Flatpak sandbox. The **DNS-over-HTTPS** analogue has **no equivalent signal**
and is deliberately left undetected on desktop:

- **systemd-resolved is DoT-only.** There is no `DNSOverHTTPS` property or method
  on the `resolve1` interface; native DoH in resolved is an open, unimplemented
  request (systemd #8639 / #42399). A DoH user runs a *separate* local forwarder
  (cloudflared, dnscrypt-proxy, AdGuardHome, …) that resolved isn't even aware of.
- **Local DoH forwarders expose no standard D-Bus interface** to query, and there
  is **no xdg-desktop-portal for DNS/resolver config** (ProxyResolver is
  HTTP/SOCKS only; NetworkMonitor exposes connectivity booleans, not resolver
  config).
- The only host-access paths that would surface a DoH signal are
  **sandbox-escape-tier** (`--talk-name=org.freedesktop.Flatpak` host-spawn, i.e.
  arbitrary host command execution) or brittle, proxy-specific config-file reads
  that reveal *config presence*, not active DoH use. Both are disproportionate
  sandbox-weakening for a best-effort, false-positive-prone heuristic, and
  directly contradict this app's minimal-host-access security posture.

So the cross-platform cause vocabulary keeps `PrivateDnsBlocking` shared (desktop
covers the DoT case), and the DoH case is a **documented desktop gap** rather
than a fuzzy probe. (Investigated 2026-07-22; conclusion: defer.) A possible
future, in-sandbox angle unrelated to DoH: the NetworkMonitor portal's
`captive-portal` connectivity enum value.

---

## Resolved

### BLOCKER-DESK-004 (RESOLVED 2026-07-07) — NM `Ip4Connectivity` wire-contract now covered outside the privileged harness

**Files:** `desktop/gatepath/portal_monitor.py`,
`desktop/tests/test_nm_property_contract.py` (new),
`desktop/tests/test_nm_dbusmock_connectivity.py` (new),
`.github/workflows/desktop.yml`.

**What this closed:** `network_manager.rs` (the Rust helper) has always read
the correct `Ip4Connectivity` property, but `portal_monitor.py` (the Python
desktop client) was reading the bare `Connectivity` property, which does not
exist on NetworkManager >=1.16 (`org.freedesktop.DBus.Error.InvalidArgs` on a
real bus). This is exactly the class of regression this entry used to warn
about — it shipped because the only two things that read this property were
the privileged `tests/e2e-hwsim/` harness (can't run in CI) and
`test_captive_interface_lookup.py`, which only pinned a hand-rolled fake and
never touched the real property name. Fixed: `portal_monitor.py` now reads
`Ip4Connectivity`, matching the Rust side.

**Resolution:** two new test layers, neither needing netns/kernel-module
privilege:
- `test_nm_property_contract.py` — dependency-free (`sys.modules`-injected
  fake `dasbus.connection`), runs everywhere pytest does; verified to fail
  (RED) against the pre-fix `device.Connectivity` read and pass (GREEN)
  against `device.Ip4Connectivity`.
- `test_nm_dbusmock_connectivity.py` — `python-dbusmock`-backed integration
  test per the original fix plan: stands up a fake NetworkManager on a
  private system bus (`DBusTestCase.start_system_bus()`, no root) and
  exercises `NMCaptiveInterfaceLookup` against it directly. Skips (not
  fails) when `python-dbusmock`/`dasbus`/`dbus-daemon` aren't present;
  `.github/workflows/desktop.yml`'s `pytest` job now installs all three so
  it runs for real in CI.

**Not yet closed:** the Rust-side `network_manager.rs` read still has no
non-privileged automated coverage of its own (it was already correct, so
nothing forced a companion Rust-side dbusmock test this round) — the
`tests/e2e-hwsim/` harness remains the only thing exercising it. Worth a
follow-up if the Rust proxy definitions ever change.

### BLOCKER-DESK-003 (RESOLVED 2026-06-06) — Privileged exec paths validated on `mac80211_hwsim`

**Files:** `desktop/gatepath-netns-helper/src/netns.rs`
(`LinuxNetnsOps::move_interface` → `iw`), `src/connectivity.rs`
(`LinuxNetnsConnectivity` → `wpa_supplicant` + DHCP client).

**Resolution:** The `tests/e2e-hwsim/` harness drives the **real** privileged
helper against a `mac80211_hwsim` virtual radio — the real kernel Wi-Fi stack
(nl80211/cfg80211): PHY move into a throwaway `gatepath` netns → in-netns
`wpa_supplicant` re-association → DHCP → portal WebView runner → teardown.
The no-leak invariant is asserted: a trusted-net sentinel is **UNREACHABLE**
from inside the netns while the captive portal IS reachable. Green and
reproducible (3/3) on real hardware (Bazzite). See `tests/e2e-hwsim/README.md`.

**Remaining confirmation items** (not blockers; de-risked, not eliminated):

- [ ] `iw phy <phyN> set netns name gatepath` accepted by physical-card `iw`
      version (older `iw` may only support `set netns <pid>`).
- [ ] Wireless netdev keeps its name after PHY move on real firmware (no udev
      rename inside the bare netns).
- [ ] DHCP completes on a real open captive AP (one-shot client exits 0).
- [ ] systemd hardening compatible with in-netns children on physical hardware
      (`AF_PACKET` present, `IPAddressDeny` not blocking DHCP).
- [ ] **DESK-003 C4 transient WebView unit**: `systemd-run` joins the netns via
      `NetworkNamespacePath=`, drops to caller via `--uid`, WebKit JIT runs under
      `MemoryDenyWriteExecute=no` on that unit while the helper keeps W^X.
- [ ] **DESK-004 display-env plumbing**: WebView connects to Wayland/X socket
      and renders from inside the netns on real hardware.
- [ ] Teardown leaves no stray privileged processes on physical hardware.
- [x] `cargo audit` runs in CI — done.
- [x] Detect captive network's security from NetworkManager — done.
- [x] Move connectivity teardown out from under the `active` lock — done.
- [x] Restore `MemoryDenyWriteExecute` on the helper proper — implemented
      (DESK-003 C4); exec-path confirmed by hwsim harness.

**Known limitation — non-UTF-8 SSIDs:** `active_ssid` returns a lossy UTF-8
`String`; plumb raw `Vec<u8>` end-to-end if a real captive network with a
non-UTF-8 SSID is encountered (rare).

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
file (`android/app/src/main/java/com/ventouxlabs/gatepath/BindWatchdog.kt`) so
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
