<!-- Generated: 2026-07-05 | Files scanned: ~90 source files | Token estimate: ~650 -->

# Gatepath — Architecture Codemap

Multi-platform captive-portal handler. Three independently-shipped surfaces
sharing one threat model (isolate the captive-portal negotiation from the
user's normal traffic/VPN):

```
android/            Kotlin/Compose app — VpnService-based leak detection,
                     no root/privileged helper (OS-level isolation)
desktop/gatepath/    Python GTK app (Flatpak) — UI, session/portal orchestration
desktop/gatepath-netns-helper/
                     Rust — privileged root/D-Bus daemon, does the actual
                     network-namespace isolation the Python app requests
mockportal/          Standalone Flask-ish test captive portal used by e2e
tests/e2e-*          docker / android-emulator / hwsim (real-hardware) suites
distribution/        F-Droid + Flathub packaging metadata
```

## Desktop data flow
```
gatepath/app.py (run_app)
  → window.py (GTK window/UI)
  → session_controller.py (SessionController: arm/close/timeout state machine)
      → portal_session.py (PortalSession)
      → session_timer.py (idle/timeout countdown)
  → portal_monitor.py (NM D-Bus device-state polling → detects captive iface)
  → netns_client.py (NetnsClient: D-Bus proxy to the privileged helper)
      —[system D-Bus]→ gatepath-netns-helper (Rust, root)
  → portal_webview.py / portal_webview_runner.py (WebKitGTK view launched
      inside the isolated netns, spawned by the helper)
  → vpn_detector.py (detect active VPN before isolating, avoid full-tunnel conflicts)
  → blocked_domains.py / audit_log.py (tracker-domain block list + redacted audit trail)
```

## Desktop privileged helper (Rust) — see backend.md for D-Bus method map
Runs as root; unprivileged GTK app is the only caller (PolicyKit-gated).

## Android data flow
```
GatepathApplication / MainActivity → MainViewModel
  → network/CaptivePortalMonitor.kt (connectivity + captive URL detection)
  → network/VpnDetector.kt + network/VpnHeuristics.kt (pure, unit-tested heuristic
      object — extracted 2026-07-05, see recent refactor(android) commit)
  → session/PortalSessionManager.kt + session/PortalSession.kt
  → ui/PortalScreen.kt, ui/GatepathWebView.kt (in-app captive webview)
  → diag/DiagnosticEngine.kt + diag/*Probe.kt (HTTP/DNS captive diagnostics)
  → share/DiagnosticsSharer.kt (ACTION_SEND redacted support bundle)
  → audit/AuditLog.kt, audit/AuditEntry.kt
  → service/PortalMonitorService.kt (foreground service)
```
No root helper on Android — isolation relies on `VpnService` + OS APIs, not netns.

## Cross-cutting invariants
- Both desktop and Android maintain **parallel audit-log schemas** — see
  `docs/AUDIT_LOG_SCHEMA.md` / `docs/audit_log_schema.json`; a cross-language
  drift guard (P1.1, PR #57) keeps `RefusalReason` enums in sync between
  `netns_client.py` and the Rust `dbus_service.rs`.
- Identity: Android/F-Droid app id `com.ventouxlabs.gatepath` (lowercase);
  desktop D-Bus/Flatpak id `com.ventouxlabs.Gatepath` (capital G). Crate name
  `gatepath-netns-helper` unchanged.
- Full docs index: `docs/ARCHITECTURE.md`, `docs/SECURITY_MODEL.md`,
  `docs/ISOLATION_BACKENDS.md`, `docs/ROADMAP.md`, `docs/BLOCKERS.md`.
