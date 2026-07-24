<!-- Generated: 2026-07-23 | Files scanned: ~120 source files | Token estimate: ~720 -->

# Gatepath — Architecture Codemap

Multi-platform captive-portal handler. Three independently-shipped surfaces
sharing one threat model (isolate the captive-portal negotiation from the
user's normal traffic/VPN):

```
android/            Kotlin/Compose app — VpnService-based leak detection,
                     no root/privileged helper (OS-level isolation)
desktop/gatepath/    Python GTK app (Flatpak) — UI, session/portal orchestration,
                     diagnostics engine
desktop/gatepath-netns-helper/
                     Rust — privileged root/D-Bus daemon, does the actual
                     network-namespace isolation the Python app requests
                     (+ fuzz/ — out-of-CI cargo-fuzz targets for its validators)
mockportal/          Standalone stdlib captive portal used by every test layer
tests/e2e-*          docker / android-emulator / hwsim (real-hardware) suites
distribution/        F-Droid + Flathub packaging metadata; fastlane/ store text
```

## Desktop data flow
```
gatepath/app.py (run_app)
  → window.py (GTK window/UI + diagnosis panel + VPN banner)
  → session_controller.py (SessionController: arm/close/timeout state machine)
      → portal_session.py (PortalSession)
      → session_timer.py (idle/timeout countdown)
  → portal_monitor.py (Monitor = polling fallback; NMSignalMonitor =
      event-driven NM StateChanged subscription → re-probe on change)
  → portal_launcher.py (PortalLauncher: detection → window.open_portal, GTK-loop
      marshalled, re-entrancy guarded)
  → netns_client.py (NetnsClient: D-Bus proxy to the privileged helper)
      —[system D-Bus]→ gatepath-netns-helper (Rust, root)
  → portal_webview.py / portal_webview_runner.py (WebKitGTK view launched
      inside the isolated netns, spawned by the helper)
  → vpn_detector.py (detect active VPN before isolating, avoid full-tunnel conflicts)
  → diagnosis_runner.py (async battery runner) → diag/ (pure probe package,
      injected ProbeContext) + diag_context.py / http_fetcher.py (platform reads)
  → blocked_domains.py / audit_log.py (tracker-domain block list + redacted audit trail)
```
Diagnostics: `diag/` is a **pure** package (probes over an injected
`ProbeContext`, no I/O imports — CI-enforced); all platform reads live in
`diag_context.py` (NM + resolve1 D-Bus) and `http_fetcher.py`.

## Desktop privileged helper (Rust) — see backend.md for D-Bus method map
Runs as root; unprivileged GTK app is the only caller (PolicyKit-gated). Its five
input validators are the trust boundary — proptest (in-CI) + cargo-fuzz (out-of-CI).

## Android data flow
```
GatepathApplication / MainActivity → MainViewModel
  → network/CaptivePortalMonitor.kt (connectivity + captive URL detection)
  → network/VpnDetector.kt + network/VpnHeuristics.kt (pure, unit-tested heuristics)
  → network/PortalProbe.kt + network/HttpFetcher.kt + network/BoundedReader.kt
      (HTTP captive probe + bounded, byte-capped reads)
  → session/PortalSessionManager.kt + session/PortalSession.kt
  → ui/PortalScreen.kt, ui/GatepathWebView.kt (in-app captive webview)
  → diag/DiagnosticEngine.kt + diag/*Probe.kt (12-cause HTTP/DNS/clock/proxy battery)
  → ui/DiagnosisPanel.kt (renders DiagnosisResult)
  → share/DiagnosticsSharer.kt (ACTION_SEND redacted support bundle)
  → audit/AuditLog.kt, audit/AuditEntry.kt
  → service/PortalMonitorService.kt (foreground service)
```
No root helper on Android — isolation relies on `VpnService` + OS APIs, not netns.

## Cross-cutting invariants (machine-checked, not commented)
- **Parallel audit-log schemas** (desktop ↔ Android) — `docs/audit_log_schema.json`;
  `schema-parity.yml` enforces both writers conform. See data.md.
- **D-Bus `RefusalReason` enum** kept in sync via a source-parsing drift guard
  (`test_netns_client.py` ↔ `dbus_service.rs`).
- **D-Bus method/signal contract** pinned by `docs/netns_helper_dbus_contract.json`
  + `dbus-contract-parity.yml` (Rust introspects the real zbus interface with no
  bus; Python pins client arities/error prefix). See backend.md.
- **Diagnosis cause parity** (`test_cause_parity.py`) parses the Kotlin
  `DiagnosticReport` variants and asserts desktop `Cause` = Kotlin − Android-only.
- Identity: Android/F-Droid app id `com.ventouxlabs.gatepath` (lowercase);
  desktop D-Bus/Flatpak id `com.ventouxlabs.Gatepath` (capital G). Crate
  `gatepath-netns-helper`.
- **Releases:** `release.yml` keyless-cosign-signs every artifact (Android
  AAB/APK/SBOM + desktop sysext `.raw` + Flatpak) on a `v*` tag. See RELEASING.md.
- Full docs index: `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`,
  `docs/ISOLATION_BACKENDS.md`, `docs/ROADMAP.md`, `docs/BLOCKERS.md`.
