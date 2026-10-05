<!-- Updated: 2026-10-04 | Source map: Android, desktop Python, Rust helper, packaging and CI -->

# Gatepath — Architecture Codemap

Multi-platform captive-portal handler. Android and desktop share an audit schema
and the goal of isolating portal traffic from normal traffic/VPNs. Desktop
confinement requires the native helper and host portal runtime; fallback browsing
uses the host route and is recorded as unconfined.

```
android/            Kotlin/Compose app — captive-Network binding/classification,
                     no root/privileged helper
desktop/gatepath/    Python GTK app (native RPM or Flatpak) — UI, session/portal orchestration,
                     diagnostics engine
desktop/gatepath-netns-helper/
                     Rust — privileged root/D-Bus daemon, does the actual
                     network-namespace isolation the Python app requests
                     (+ fuzz/ — validator cargo-fuzz targets; scheduled CI soaks)
mockportal/          Standalone stdlib captive portal used by every test layer
tests/e2e-*          docker / android-emulator / hwsim (virtual-radio) suites
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
  → blocked_domains.py / audit_log.py (tracker-domain observations + local audit trail;
      raw network identifiers redacted when collecting a support bundle)
```
Diagnostics: `diag/` is a **pure** package (probes over an injected
`ProbeContext`, no I/O imports — CI-enforced); all platform reads live in
`diag_context.py` (NM + resolve1 D-Bus) and `http_fetcher.py`. Desktop diagnostics
use the caller's network route; helper confinement applies to portal browsing.

## Desktop privileged helper (Rust) — see backend.md for D-Bus method map
Runs as root; unprivileged GTK app is the only caller (PolicyKit-gated). Its five
input validators are the trust boundary — PR proptests plus scheduled
`fuzz.yml` cargo-fuzz soaks.

## Android data flow
```
GatepathApplication / MainActivity → MainViewModel
  → CaptivePortalActivity (system sign-in handoff; preserves page/binding on fold/rotation)
  → network/ConfinementState.kt (classification) + network/ProcessBinding.kt (leases)
  → network/CaptivePortalMonitor.kt (connectivity + captive URL detection)
  → network/VpnDetector.kt + network/VpnHeuristics.kt (pure, unit-tested heuristics)
  → network/PortalProbe.kt + network/HttpFetcher.kt + network/BoundedReader.kt
      (HTTP captive probe + bounded, byte-capped reads)
  → session/PortalSessionManager.kt + session/PortalSession.kt
      + session/SessionIncidentState.kt (accepted incident attribution + terminal resets)
  → ui/PortalScreen.kt, ui/GatepathWebView.kt (in-app captive webview)
  → diag/DiagnosticEngine.kt + diag/*Probe.kt (12-cause HTTP/DNS/clock/proxy battery)
  → ui/DiagnosisPanel.kt (renders DiagnosisResult)
  → share/DiagnosticsSharer.kt (ACTION_SEND redacted support bundle)
  → audit/AuditLog.kt, audit/AuditEntry.kt
  → service/PortalMonitorService.kt (foreground service)
```
No root helper on Android: portal traffic uses the captive `Network` and
process-binding leases, with classification gating sign-in. `tests/e2e-android/`
uses a test VPN sink to check traffic; the application does not provide a VPN.

## Native desktop packaging
`packaging/build-rpm.sh` builds the `gatepath-netns-helper` RPM containing the
Python app, desktop launcher/assets, fixed portal runner, privileged helper and
service/policy files. GTK/WebKit/Python GUI dependencies are required; Fedora CI
builds and installs it with optional dependencies disabled. The sysext supplies
the helper and runner wrapper only; its host Python app/runtime must be installed
separately. Physical Wi-Fi/open captive AP validation remains pending (#45).

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
