<!-- Generated: 2026-07-05 | Files scanned: 29 Kotlin + 6 Python UI files | Token estimate: ~500 -->

# Frontend Codemap — Android (Compose) + Desktop (GTK)

## Android (android/app/src/main/java/com/ventouxlabs/gatepath/)
```
GatepathApplication (app class, DI root)
MainActivity → MainViewModel (@Inject constructor)
  ├── network/CaptivePortalMonitor.kt   — connectivity + captive-URL detection
  ├── network/VpnDetector.kt            — stateful VPN check, uses…
  ├── network/VpnHeuristics.kt          — pure heuristic object (extracted 2026-07-05
  │                                        from VpnDetector for unit testability)
  ├── network/BlockedDomains.kt         — tracker block list
  ├── network/PortalProbe.kt            — HTTP captive probe
  ├── network/NetworkDiagnostics.kt
  ├── session/PortalSessionManager.kt + session/PortalSession.kt
  ├── diag/DiagnosticEngine.kt (DiagnosisResult) + diag/{Http,PrivateDns}Probe.kt
  │        + diag/{DiagnosticModule,DiagnosticProbe,DiagnosticReport,
  │                DiagnosticsBundle,ProbeContext,RecommendedAction}.kt
  ├── share/DiagnosticsSharer.kt         — ACTION_SEND redacted bundle (P3.1, #66)
  ├── audit/AuditLog.kt + audit/AuditEntry.kt
  ├── service/PortalMonitorService.kt    — foreground service host
  ├── BindWatchdog.kt                    — service-binding liveness guard
  └── di/AppModule.kt                    — Hilt/DI bindings
```

### Compose UI layer (network/ui/, ui/)
```
ui/MainScreen.kt        — top-level Compose screen
ui/PortalScreen.kt       — captive-portal-in-progress screen
ui/GatepathWebView.kt    — Compose wrapper around Android WebView
ui/DiagnosisPanel.kt     — shows DiagnosticEngine results
ui/WebViewHostMatching.kt — captive-portal host allow/redirect matching
ui/theme/Theme.kt        — Material theme
CaptivePortalActivity.kt — hosts the in-app portal webview
```
No separate ViewModel-per-screen split observed; `MainViewModel` is the
primary state holder. Build: `android/app/build.gradle.kts`, root
`android/build.gradle.kts`. AGP 9 / Kotlin built-in / compileSdk 37 (see
memory `dependabot-triage-and-agp9-migration`).

## Desktop GTK UI (desktop/gatepath/)
```
window.py                  — main GTK window/status UI (243 lines)
portal_webview.py           — WebKitGTK view controller
portal_webview_runner.py    — process entry point run inside the isolated netns
```
No component framework — plain GTK widget tree built in `window.py`. UI talks
to `session_controller.py` / `netns_client.py` for all state and privileged
actions (see backend.md).
