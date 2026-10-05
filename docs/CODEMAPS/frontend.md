<!-- Updated: 2026-10-04 | Source map: Android Compose/handoff and desktop GTK UI -->

# Frontend Codemap — Android (Compose) + Desktop (GTK)

## Android (android/app/src/main/java/com/ventouxlabs/gatepath/)
```
GatepathApplication (app class, DI root)
MainActivity → MainViewModel (@Inject constructor)
CaptivePortalActivity — system captive-portal handoff, classified sign-in;
                        configChanges preserves binding/page on fold/rotation
  ├── network/CaptivePortalMonitor.kt   — connectivity + captive-URL detection
  ├── network/VpnDetector.kt            — stateful VPN check, uses…
  ├── network/VpnHeuristics.kt          — pure heuristic object (unit-tested)
  ├── network/BlockedDomains.kt         — tracker block list
  ├── network/PortalProbe.kt            — HTTP captive probe
  ├── network/HttpFetcher.kt            — HTTP client for probes
  ├── network/BoundedReader.kt          — byte-capped reader (8 MiB body bound)
  ├── network/NetworkDiagnostics.kt
  ├── session/PortalSessionManager.kt + session/PortalSession.kt
  ├── session/SessionIncidentState.kt   — JVM-tested attribution and terminal resets
  ├── network/ConfinementState.kt       — state model and pure classify function
  ├── network/ProcessBinding.kt         — shared process-binding leases
  ├── diag/DiagnosticEngine.kt (DiagnosisResult) + diag/DiagnosticReport.kt
  │        + 12-cause probe set: {Http,HttpProxy,HttpsOnly,DnsHijack,NoDns,
  │          RedirectLoop,ClockSkew,PrivateDns,Vpn,CellularFallback}Probe.kt
  │        + diag/{DiagnosticModule,DiagnosticProbe,DiagnosticsBundle,
  │                ProbeContext,RecommendedAction}.kt
  ├── share/DiagnosticsSharer.kt         — ACTION_SEND redacted bundle (P3.1)
  ├── audit/AuditLog.kt + audit/AuditEntry.kt
  ├── service/PortalMonitorService.kt    — foreground service host
  ├── BindWatchdog.kt                    — service-binding liveness guard
  └── di/AppModule.kt                    — Hilt/DI bindings
```

### Compose UI layer (ui/)
```
ui/MainScreen.kt        — top-level Compose screen (+ Share-diagnostics entry)
ui/PortalScreen.kt       — captive-portal-in-progress screen
ui/GatepathWebView.kt    — Compose wrapper; client refreshed when portal host changes
ui/DiagnosisPanel.kt     — renders DiagnosticEngine results
ui/WebViewHostMatching.kt — captive-portal host allow/redirect matching
ui/theme/                — Material theme
```
`MainViewModel` is the primary state holder (no ViewModel-per-screen split).
The WebView retains its page during the handoff Activity's handled configuration
changes. Console diagnostics use `ConsoleCaptureBuffer` / `ConsoleCaptureFile`
(bounded capture, forward-compatible JSON decoding) and redacted sharing.
Build: `android/app/build.gradle.kts`; AGP 9 / Kotlin built-in / compileSdk 37
(see memory `dependabot-triage-and-agp9-migration`).

## Desktop GTK UI (desktop/gatepath/)
```
window.py                  — main GTK window/status UI + diagnosis panel +
                             VPN banner (plain GTK widget tree)
portal_webview.py           — WebKitGTK view controller
portal_webview_runner.py    — process entry point run inside the isolated netns
```
Diagnosis panel = a "Run diagnostics" button rendering `diagnosis_runner` output;
Adw row/banner titles are Pango markup, so network-derived text is escaped
(`_safe_markup` in the pure panel, `GLib.markup_escape_text` in the gi window).
No component framework — UI talks to `session_controller.py` / `portal_launcher.py`
/ `netns_client.py` for all state and privileged actions (see backend.md).
Native RPM installs this UI and the host runner together. Flatpak UI can use the
host helper when available; fallback portal browsing stays on the normal route.
