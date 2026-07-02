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

## Debug intent (BuildConfig.DEBUG only)

`MainActivity` accepts a debug-only extra that jumps the ViewModel
straight to `PortalSession.Active` against a chosen URL, bypassing the
captive-detection pipeline. Stripped from release builds.

```
adb install -r app-debug.apk
adb shell am start \
    -n com.ventouxlabs.gatepath/.MainActivity \
    --es gatepath.debug.portal_url "http://<reachable-host>/portal"
```

`PortalScreen` opens; `GatepathWebView` loads the URL; the off-domain
resource-blocking policy is exercised; `Dismiss` returns to `Idle`.

What this path does **not** exercise:
- `PortalSessionManager` state transitions
- `CaptivePortalMonitor` event handling
- Audit log writes (PortalCompleted / Dismissed / Timeout)
- VPN warning / DiagnosticEngine
- The system `CAPTIVE_PORTAL` intent dispatch path

For those, use a stock-Android device or an emulator harness against a
real captive Wi-Fi setup.

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

```sh
(cd android && ANDROID_HOME="$ANDROID_HOME" ./gradlew :app:assembleDebug)
cd tests/e2e-android && ./run-e2e.sh
```

Requires `/dev/kvm` on the host. CI uses
`reactivecircus/android-emulator-runner` instead — see
`.github/workflows/android-e2e.yml`. AOSP only; the GrapheneOS quirk
above doesn't apply to emulator images.
