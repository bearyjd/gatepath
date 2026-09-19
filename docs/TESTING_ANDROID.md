# Testing the Android Captive-Portal Flow

How to exercise `PortalScreen` / `GatepathWebView` without real captive Wi-Fi.

## The GrapheneOS quirk

The system `CAPTIVE_PORTAL` intent flow cannot be triggered on a
GrapheneOS device against a local mock portal. GrapheneOS's patched
`com.android.networkstack` APK hardcodes the captive-portal probe URLs
as Java constants pointing at `connectivitycheck.grapheneos.network`.
The standard overrides:

```
settings put global captive_portal_http_url   http://your-portal/...
settings put global captive_portal_https_url  http://your-portal/...
settings put global captive_portal_fallback_url http://your-portal/...
```

are accepted by `settings put` and visible in `settings list global`,
but **NetworkStack never reads them** — it uses the build-time constants
instead. There is no user-facing Settings knob to plug in a custom probe
URL. Confirm a suspected device with:

```
adb logcat -d | grep NetworkMonitor
# Look for: PROBE_HTTP http://connectivitycheck.grapheneos.network/...
```

The same constraint likely applies to other privacy-focused Android forks
that mod NetworkStack (CalyxOS, e/OS, LineageOS with patches).

Synthesising the `CAPTIVE_PORTAL` intent manually via `am start` is also
not viable: `CaptivePortalActivity` requires the system-supplied
`CaptivePortal` parcelable token and `finish()`es immediately if it is
absent. See `CaptivePortalActivity.kt`.

## Debug intents (BuildConfig.DEBUG only)

`MainActivity` accepts several debug-only extras, all stripped from release
builds:

- `gatepath.debug.portal_url` — jumps the ViewModel straight to
  `PortalSession.Active` against a chosen URL, bypassing the
  captive-detection pipeline entirely (so the session is never confined —
  see the `confinement` audit field below).
- `gatepath.debug.write_bundle` (+ `gatepath.debug.redact`) — writes the
  diagnostics bundle to `cache/diagnostics/gatepath-diagnostics.txt` and the
  share URI to `files/debug-bundle-uri.txt`, without going through the share
  sheet.
- `gatepath.debug.sentinel_probe` — fires one bound-network sentinel probe
  on demand. This is the mechanism the `tests/e2e-android` `covering`-mode
  no-leak proof uses for its liveness/confinement markers instead of the old
  UDP burst (see `tests/e2e-android/HARNESS_NOTES.md`).

```
adb install -r app-debug.apk
adb shell am start \
    -n com.ventouxlabs.gatepath/.MainActivity \
    --es gatepath.debug.portal_url "http://<reachable-host>/portal"
```

`PortalScreen` opens; `GatepathWebView` loads the URL; the off-domain
resource-blocking policy is exercised; `Dismiss` returns to `Idle`.

What the `portal_url` path does **not** exercise:
- `PortalSessionManager` state transitions
- `CaptivePortalMonitor` event handling
- Audit log writes (PortalCompleted / Dismissed / Timeout)
- VPN warning / DiagnosticEngine
- `ConfinementState` classification — the debug-forced session never
  classifies, so its audit entry (if any) always carries
  `confinement: unconfined`
- The system `CAPTIVE_PORTAL` intent dispatch path

For those, use a stock-Android device or an emulator harness against a
real captive Wi-Fi setup.

To read the live classification off a debug build without waiting on the UI:

```
adb shell run-as com.ventouxlabs.gatepath cat files/confinement-state.txt
```

This is the same sidecar `tests/e2e-android/driver/assertions.py` polls; it
holds one of `confined` / `tunnelled` / `blocked` / `dns_strict` / `unknown`
(`ConfinementState.schemaName`), written by `MainViewModel.debugStateSink` on
every classified incident.

## Mock portal

The desktop e2e harness at `tests/e2e-docker/` packages the mock portal
under `mockportal/`. The module's default `PORTAL_HOST = "127.0.0.1"` is
a deliberate safeguard — `/log` echoes request headers verbatim and
exposing it on a network can leak `Authorization` tokens.

To run it on a non-loopback address for ad-hoc Android testing, write a
launcher that calls `build_server(host=<your-trusted-bind>, port=…,
complete_after=…)` directly. Keep it on a trusted network (tailnet, etc.)
and stop it when done.

## Reachability caveat

`GatepathWebView` binds its traffic to the `Network` argument passed by
the activity. The debug path passes `ConnectivityManager.activeNetwork`.
If the active network is a VPN (e.g. Tailscale), traffic to a tailnet IP
works. If the active network is bare Wi-Fi, the portal URL must be
reachable from a Wi-Fi-bound socket — Tailscale-only addresses will not
resolve. Either host the portal on the Wi-Fi-reachable LAN, or join the
phone to the same VPN.

## Local emulator harness (AOSP)

For full system-flow coverage — including the parts the debug intent
skips (`CAPTIVE_PORTAL` parcelable, `ConnectivityManager.bindProcessToNetwork`,
the chooser → activity → `reportCaptivePortalDismissed` round-trip) —
use `tests/e2e-android/`. It boots an AOSP Android 14 emulator under
Docker, points `Settings.Global.captive_portal_*_url` at a local
mockportal reachable via `10.0.2.2:18080`, drives the chooser via
UIAutomator, and asserts the full path against scenario / audit / gateway
logs (mirrors `tests/e2e-docker/`'s shape).

The scenario runs in one of two VPN modes (`--vpn-mode`), which now require
a second APK, the standalone debug-only `android/testvpn/` app
(`--testvpn-apk-path`, required):

- `covering` — the `:testvpn` app covers Gatepath as a third-party secure
  VPN. Expect `ConfinementState.Tunnelled`: no portal session opens, no
  `/portal` request reaches the mock, and the no-leak VPN sink proves
  confinement across the settled bound window.
- `excluding` — Gatepath is excluded from the VPN's disallowed-app list,
  the shipped product contract. Expect `ConfinementState.Confined`: the
  monitor opens the session on its own (no debug intent), sign-in
  completes, and the resulting audit entry carries `confinement: confined`.

```sh
(cd android && ANDROID_HOME="$ANDROID_HOME" ./gradlew :app:assembleDebug :testvpn:assembleDebug)
cd tests/e2e-android && ./run-e2e.sh
```

Requires `/dev/kvm` on the host. CI uses
`reactivecircus/android-emulator-runner` instead, as a `{covering,
excluding}` matrix — see `.github/workflows/android-e2e.yml`. AOSP only; the
GrapheneOS quirk above doesn't apply to emulator images.

## Physical confinement matrix

The emulator harness proves the classification pipeline against a debug VPN
it controls. It cannot prove real client behaviour — actual VPN apps enforce
`allowBypass`/split-tunnelling in ways only a physical device and a real
client exercise. This checklist is manual and unautomated; run it on a
device before relying on a claim about a specific VPN client.

Read the live classification after each cell with:

```sh
adb shell run-as com.ventouxlabs.gatepath cat files/confinement-state.txt
```

and share the evidence bundle (`Share diagnostics` in-app) for any cell that
doesn't match the expected state.

**VPN client × inclusion, expect `Tunnelled` / `Confined`:**

| VPN client | Gatepath included | Gatepath excluded |
|---|---|---|
| Tailscale (tailnet-only) | Tunnelled | Confined |
| Tailscale (exit node) | Tunnelled | Confined |
| TorGuard | Tunnelled | Confined |

**Private DNS × portal address form, expect `DnsStrict` only in the
strict × hostname cell:**

| Private DNS | Hostname portal | IP-literal portal |
|---|---|---|
| Automatic | Confined | Confined |
| Strict | DnsStrict | Confined |

Devices used: Pixel 9 Pro Fold, Pixel 10 Pro Fold.
