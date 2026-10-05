# Gatepath Android

Captive-portal handler for Android. Isolates portal sign-in traffic to the
captive-portal network interface, **observes** tracker/analytics requests in
the portal WebView (counted in the audit log; allowed to load so captive
vendors' GA/GTM embeds don't break the Continue button), wipes cookies +
DOM storage + cache on session close, and writes an append-only audit log.

## Build prerequisites

| Dependency | Version | Notes |
|---|---|---|
| JDK | 21+ | Set `JAVA_HOME` |
| Android SDK | API 37 (compileSdk), target API 35 | Set `ANDROID_HOME` |
| Android Build Tools | Selected by AGP | Installed via SDK Manager |
| Gradle | 9.8.0 | Managed by the wrapper (`./gradlew`) |
| Kotlin compiler plugins | 2.4.20 | AGP 9.4.1 provides built-in Kotlin |

You do **not** need to install Gradle or Kotlin separately — the Gradle wrapper
downloads them on first run.

## Quick start

```bash
# Build debug APK
./gradlew assembleDebug

# Run unit tests (on JVM, no emulator)
./gradlew test

# Run instrumented tests (requires connected device or emulator)
./gradlew connectedAndroidTest
```

## JVM-only tests (no Android SDK)

A subset of tests covering pure-Kotlin business logic can run without the
Android SDK using `kotlinc` + JUnit:

```bash
./run-jvm-tests.sh
```

The script will:
1. Check for `kotlinc` on `PATH` (exits with a clear message if missing).
2. Download required JARs from Maven Central into `~/.cache/gatepath-test-jars/`.
3. Compile the JVM-compatible source subset + test sources.
4. Run tests via the JUnit Platform Console Launcher.

**Requires:** JDK 21, `kotlinc` 2.0.x, `python3` (for `PortalProbeTest`),
and internet access (first run only, to download JARs).

## Module layout

```
android/
├── build.gradle.kts          Top-level plugins block
├── settings.gradle.kts       Module graph + version catalog source
├── gradle.properties         JVM args, AndroidX flags
├── gradle/
│   ├── libs.versions.toml    Version catalog (Kotlin, AGP, Compose, Hilt)
│   └── wrapper/              Gradle 9.8.0 wrapper
├── gradlew / gradlew.bat     Wrapper scripts
├── run-jvm-tests.sh          JVM-only test driver
└── app/
    ├── build.gradle.kts      App 1.1.0: minSdk 29, targetSdk 35, compileSdk 37, R8 on release
    ├── proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   └── java/com/ventouxlabs/gatepath/
        │       ├── GatepathApplication.kt      @HiltAndroidApp, AuditLog.init()
        │       ├── MainActivity.kt             @AndroidEntryPoint, Compose root
        │       ├── MainViewModel.kt            Session orchestration, audit writes
        │       ├── CaptivePortalActivity.kt    System sign-in handoff; preserves fold/rotation page
        │       ├── di/AppModule.kt             Hilt bindings
        │       ├── network/
        │       │   ├── CaptivePortalMonitor.kt NetworkCallback → Flow<NetworkEvent>
        │       │   ├── PortalProbe.kt          HTTP probe (Network.openConnection)
        │       │   ├── VpnDetector.kt          Best-effort VPN/Tailscale detection
        │       │   └── BlockedDomains.kt       Tracker domain list (pure Kotlin)
        │       ├── session/
        │       │   ├── PortalSession.kt        Sealed state hierarchy
        │       │   ├── SessionIncidentState.kt Accepted attribution + terminal reset paths
        │       │   └── PortalSessionManager.kt Immutable state transitions
        │       ├── ui/
        │       │   ├── MainScreen.kt           Status / idle Composable
        │       │   ├── PortalScreen.kt         Full-screen portal Composable
        │       │   ├── GatepathWebView.kt      Hardened WebView Composable
        │       │   └── theme/Theme.kt          Material3 colour scheme
        │       ├── audit/
        │       │   ├── AuditEntry.kt           Serializable data class (schema v2)
        │       │   └── AuditLog.kt             Mutex-safe JSONL writer + singleton
        │       └── service/
        │           └── PortalMonitorService.kt Foreground service (connectedDevice)
        └── test/
            └── java/com/ventouxlabs/gatepath/
                ├── PortalProbeTest.kt      Integration test vs. real mockportal server
                ├── SessionStateTest.kt     State machine transition tests
                ├── AuditLogTest.kt         Schema round-trip + concurrent writes
                └── BlockedDomainsTest.kt   Tracker-domain matching tests
```

## Permissions

Exactly these permissions are declared, per the security model:

- `ACCESS_NETWORK_STATE`
- `CHANGE_NETWORK_STATE`
- `INTERNET`
- `FOREGROUND_SERVICE`
- `FOREGROUND_SERVICE_CONNECTED_DEVICE`
- `POST_NOTIFICATIONS`
- `ACCESS_WIFI_STATE`

## Security model

See [`docs/SECURITY_MODEL.md`](../docs/SECURITY_MODEL.md) for the full threat
model. Key Android guarantees:

- Captive `Network` probes and shared process-binding leases support portal
  isolation. Normal sign-in requires a classified `Confined` state; failed or
  unknown confinement presents recovery actions. A successful binding call
  alone is not treated as proof that portal traffic bypasses a VPN.
- System sign-in preserves its binding, classification and WebView during
  fold/rotation changes. The WebView client refreshes with the current portal
  host, including its host-specific TLS error policy.
- WebView: JS enabled (required for most portals), file/content access disabled,
  `databaseEnabled` off, form-data save off, cache mode `LOAD_NO_CACHE`. Cookies
  and DOM storage (`sessionStorage` / `localStorage`) are **enabled during
  sign-in** (captive vendors stash session nonces in them) and **wiped on
  session close** via `CookieManager.removeAllCookies` + `WebStorage.deleteAllData`
  + `clearCache` + `clearFormData` + `clearHistory`.
- Off-domain navigations: **observed and counted, allowed to load** — captive
  vendors POST sign-in forms to backend hosts different from the splash page;
  hard-refusing them broke real-world sign-ins.
- Tracker/analytics sub-requests: matched against `BlockedDomains` for the
  audit-log counter, **allowed to load** — captive splash pages embed GA/GTM
  whose `ReferenceError` on intercept breaks the Continue button.
- Session auto-closes after 10 minutes.
- Every session written to `filesDir/audit.jsonl` (app-private, not world-readable).

## Audit log

See [`docs/AUDIT_LOG_SCHEMA.md`](../docs/AUDIT_LOG_SCHEMA.md).
Written to `<filesDir>/audit.jsonl` — one JSON object per line, append-only.
