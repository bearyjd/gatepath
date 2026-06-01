# Gatepath — Build Blockers

This file tracks issues that block parts of the MVP from being verified end-to-end in
the build environment. Each entry should name the file, the symptom, the diagnosis, and
the workaround (if any).

## Status

Two open blockers gate the desktop **netns isolation** path (the
Android-parity "bind portal traffic to the Wi-Fi interface" capability). See
[`DESKTOP_NETNS_DEPLOYMENT.md`](DESKTOP_NETNS_DEPLOYMENT.md) for the full
findings and the atomic-distro deployment analysis.

---

## Open

### BLOCKER-DESK-001 — Wi-Fi interface is moved with the wrong kernel operation

**File:** `desktop/gatepath-netns-helper/src/netns.rs` (`LinuxNetnsOps::move_interface`)

**Symptom:** The helper moves the captive interface into the gatepath netns with
`ip link set dev <iface> netns <name>`. On real Wi-Fi hardware this fails with
`-EOPNOTSUPP` / "Invalid argument" and the isolated session never starts.

**Diagnosis:** A wireless netdev is bound to its `wiphy` (PHY) and cannot be
moved between network namespaces on its own. The wireless stack requires moving
the **whole PHY**:

```
iw phy <phyN> set netns name <name>     # or: set netns <pid>
```

The unit/JVM-style suite does not catch this because the privileged kernel
surface is exercised through `FakeNetnsOps`, which never invokes `ip`/`iw`.

**Workaround:** None. The privileged op must switch to `iw phy … set netns`
(resolving the PHY for the validated interface first), or to the equivalent
`nl80211` `NL80211_CMD_SET_WIPHY_NETNS` netlink call.

### BLOCKER-DESK-002 — Nothing re-establishes connectivity inside the netns

**File:** `desktop/gatepath-netns-helper/src/service.rs` (setup → launch path)

**Symptom:** Even after the PHY is moved correctly (BLOCKER-DESK-001), the
gatepath netns has no usable link, so the portal page cannot load.

**Diagnosis:** NetworkManager runs in the **host** netns and can no longer
manage the moved PHY. Moving a connected wiphy drops the L2 association on most
drivers, and the DHCP lease does not travel with the PHY. The netns therefore
has an unassociated, address-less interface.

**Workaround:** None yet. Inside the gatepath netns the helper must run its own
`wpa_supplicant` (re-associate to the captive SSID) and a DHCP client (reacquire
an address + the gateway/portal route) before spawning the WebView runner, then
tear both down with the netns. This is the larger of the two items and changes
the helper's runtime dependency set (`wpa_supplicant`, a DHCP client).

---

## Resolved

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
