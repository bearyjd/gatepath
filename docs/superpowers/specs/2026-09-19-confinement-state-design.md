# Confinement state and incident evidence — design

**Date:** 2026-09-19
**Scope:** Android app (`android/`), audit-log schema (both platforms), docs.
**Status:** approved in discussion; awaiting spec review before planning.

## 1. Problem

Gatepath's Android app claims to confine captive-portal traffic to the captive
Wi-Fi via `ConnectivityManager.bindProcessToNetwork()`. For the product's own
target user (a full-tunnel VPN, see `docs/RATIONALE.md` §1) that claim is
false, and the reason is the platform, not a bug in this repo. Verified against
AOSP `main` on 2026-09-18:

1. **A secure VPN overrides explicit network selection.** netd's
   `RULE_PRIORITY_SECURE_VPN` (13000) sorts before
   `RULE_PRIORITY_EXPLICIT_NETWORK` (16000) and matches regardless of the
   `explicitlySelected` fwmark bit (`system/netd/server/RouteController.cpp`,
   `modifyVpnUidRangeRule`). A VPN that did not call `allowBypass()` therefore
   carries a Wi-Fi-bound socket into the tunnel. Neither Tailscale nor WireGuard
   for Android calls `allowBypass()`. Under always-on lockdown with the tunnel
   down, `Vpn.setVpnForcedLocked` installs a PROHIBIT rule instead.
2. **There is no "restricted captive network" for third-party apps.** The
   `CaptivePortal` token delivered with `ACTION_CAPTIVE_PORTAL_SIGN_IN` is a
   binder for `appResponse`; `ConnectivityService.startCaptivePortalAppInternal`
   grants nothing. The repo's comments attributing `EPERM` to Android marking
   captive networks restricted (`CaptivePortalMonitor.kt`, `MainViewModel.kt`,
   `NetworkDiagnostics.kt`, `CaptivePortalActivity.kt`) are wrong.
3. **Strict Private DNS cannot be bypassed by a third-party app.** The stock
   handler uses `Network.getPrivateDnsBypassingCopy()`; the resolver honours it
   only for system UIDs, `CONNECTIVITY_USE_RESTRICTED_NETWORKS`,
   `NETWORK_BYPASS_PRIVATE_DNS`, `MAINLINE_NETWORK_STACK`, or a captive delegate
   UID (`packages/modules/DnsResolver/DnsProxyListener.cpp`,
   `hasPermissionToBypassPrivateDns`).
4. **The framework's own VPN-bypass-for-login mechanism is closed to us.**
   `CaptivePortal.setDelegateUid` → `NetworkAgentInfo.setCaptivePortalDelegateUid`
   → `allowBypassVpnOnNetwork` exists (Android 15/16 custom-tabs flow) but is
   gated on `PERMISSION_MAINLINE_NETWORK_STACK`.

Consequences today: under Tailscale with an exit node or TorGuard, the bound
probe returns 204 through the tunnel, the app lands in `CaptivePending`, and the
troubleshooting text explains a mechanism that does not exist. With strict
Private DNS a hostname portal white-screens. When a Tunnelled device picks
Gatepath in the system chooser it gets a blank WebView.

## 2. Product decision

One app, two capabilities, driven by one runtime fact: **is Gatepath's traffic
confined to the captive Wi-Fi right now?**

- **Compartment (when confined).** The product contract becomes: *exclude
  Gatepath from your VPN's app list* (Tailscale ≥ 1.70 and TorGuard both offer
  app-based split tunnelling on Android). Then `bindProcessToNetwork` is
  honoured and Gatepath's traffic, and only Gatepath's, goes to Wi-Fi. The app
  verifies this every incident instead of trusting the setting.
- **Verifier / debugger (always).** Every captive incident produces an evidence
  record — probe chain, path taken, resolver comparison, certificate summary —
  whether or not sign-in was possible in-app.

Costs, stated in the docs: excluding Gatepath from the VPN is permanent, so its
connectivity probe and the diagnostic DoH query leave in the clear over the
default network at all times; and the app cannot bypass strict Private DNS, so
hostname portals need the system handler or Private DNS set to automatic for
the sign-in.

## 3. Confinement state model

New pure-Kotlin file `network/ConfinementState.kt` (no Android imports, so it
compiles under `run-jvm-tests.sh`; add it to `MAIN_SOURCES`). Sealed interface:

| State | Inputs that produce it | User sentence (one line) | Primary action |
|---|---|---|---|
| `Confined(portalUrl, capture)` | Wi-Fi-bound probe returned a redirect or a 200 intercept | "Gatepath is confined to this Wi-Fi. You can sign in here." | Sign in here |
| `Tunnelled(vpnKind)` | Bound probe returned 204, or errored, while a VPN interface is up | "Your VPN is carrying Gatepath's traffic. Exclude Gatepath in {VPN app} to sign in here, or use the system notification." | Open VPN app |
| `Blocked(errno)` | Bound probe failed with a permission error (`EPERM`/`EACCES`). Always-on lockdown state is not readable by a third-party app, so the errno alone decides; a detected VPN interface only fills in `{VPN app}` | "Your VPN's kill switch is blocking Gatepath. Exclude Gatepath in {VPN app} or use the system notification." | Open VPN app |
| `DnsStrict(portalHost)` | Bound probe reached the gateway, redirect names a hostname, `network.getAllByName(host)` fails, `LinkProperties.isPrivateDnsActive` is true | "Private DNS is strict, so {host} cannot be resolved on this Wi-Fi. Set Private DNS to Automatic for this sign-in, or use the system notification." | Open network settings |
| `Unknown(bindError, fallbackError)` | none of the above | "Gatepath could not work out what this network is doing. Share the evidence." | Share evidence |

`vpnKind` is `TailscaleExitNode`, `TailscaleTailnetOnly`, `Other(packageOrIface)`,
derived from the existing `VpnDetector` / `VpnHeuristics`. `TailscaleTailnetOnly`
is a partial-route VPN: netd falls through to the bound Wi-Fi for non-tailnet
destinations, so it normally classifies as `Confined`; it appears in
`Tunnelled` only when the bound probe still failed, and the sentence then says
"exit node or route conflict".

Rules:

- Classification is a pure function `classify(bound: ProbeResult, fallback:
  ProbeResult?, vpn: VpnInfo, privateDnsActive: Boolean, resolvedPortalHost:
  Boolean?): ConfinementState`. The monitor supplies the inputs; it does not
  decide.
- Sign-in in-app is offered **only** from `Confined`. All other states never
  create a `PortalSession.Active`.
- `CaptivePortalActivity` (system chooser entry) runs the same classification
  on the delivered `Network` before showing the WebView, and renders the
  state's sentence and action instead of the WebView when not `Confined`.
- `NetworkStatus.CaptivePending` and the static troubleshooting list in
  `MainScreen` are removed. `DiagnosticEngine` stays and its top finding is
  shown as supporting detail under the state card.
- Each state has exactly one sentence and one action, defined in one table
  next to the type (`ConfinementStateText`), unit-tested like
  `PortalLoadErrorText`.

## 4. Incident evidence

New pure-Kotlin `diag/IncidentEvidence.kt`, produced once per captive incident
in every state (today the capture is kept only when a session opens or a
suspicion fires). Fields:

| Field | Source | Notes |
|---|---|---|
| `confinement` | §3 | enum name only |
| `probe_path` | new label on every `ProbeResult` | `BOUND_WIFI`, `DEFAULT_ROUTE`, `UNKNOWN`. Also resolves the ambiguity that blocked PR #154's security assertion |
| `probe_capture` | existing `PortalProbeCapture` | unchanged fields (status, allow-listed content type, redirect signal) |
| `resolver_wifi` / `resolver_doh` | `DnsHijackProbe`, re-pointed | Under `Confined` the Wi-Fi lookup uses `network.getAllByName`; under `Tunnelled`/`Blocked` the probe declines as today. Values are IP-literal lists, byte-bounded |
| `cert_summary` | `onReceivedSslError` | `primary_error` code, `not_before`/`not_after`, `self_signed` boolean, SHA-256 fingerprint. **No subject or issuer strings** — PR #152 rule: nothing gateway-authored leaves the device |
| `vpn` | `VpnInfo` | interface names + kind |
| `private_dns_active` | `LinkProperties` | boolean |

The evidence record replaces `NetworkDiagnostics` as the bundle's core; the
`DiagnosticsBundle` redaction (SSID, gateway IP, portal domain, bare IP
literals) applies to it unchanged, and `DiagnosticsBundleTest`'s field-set
guard is extended to `IncidentEvidence`. Console capture stays debug-only.

## 5. UI and flow

`MainScreen` collapses to one status card: state sentence, primary action,
and a **Share evidence** button present in every state.

- Sign in here → `PortalScreen` (unchanged).
- Open VPN app → `PackageManager.getLaunchIntentForPackage` for the detected
  VPN (`com.tailscale.ipn`, `net.torguard.openvpn.client`; the package list is
  a single constant table, and any installed app holding `BIND_VPN_SERVICE`
  is the generic fallback); falls back to `Settings.ACTION_VPN_SETTINGS` if no
  launch intent resolves.
- Open network settings → `Settings.ACTION_WIRELESS_SETTINGS`.
- Share evidence → existing share sheet.

`MainViewModel` exposes `confinement: StateFlow<ConfinementState?>` and
`evidence: StateFlow<IncidentEvidence?>`; `latestDiagnostics`,
`latestProbeCapture` and `NetworkStatus.CaptivePending` are removed.
`CaptivePortalMonitor` emits one event per incident carrying the classification
inputs; the ViewModel calls `classify` and decides whether to open a session.

## 6. Audit schema v2 (both platforms)

`docs/audit_log_schema.json` and `AUDIT_LOG_SCHEMA.md` go to `schema_version: 2`:

- Add `confinement`: enum `confined`, `unconfined`. Android sessions only open
  from `Confined`, so Android always writes `confined`. Desktop writes
  `confined` when the netns helper launched the portal, `unconfined` for the
  Flatpak-only path.
- Rename `blocked_navigation_attempts` → `observed_navigation_attempts` and
  `blocked_resource_requests` → `observed_resource_requests` (semantics changed
  in PR #33; rename deferred until a version bump).
- Readers accept v1 and v2 (the Android bundle reader and the desktop log
  viewer); writers emit v2 only. No migration of existing files.
- `schema-parity.yml` and the desktop parity test cover the new field and the
  renames; the Android `AuditEntry` and desktop `audit_log.py` change together.

Incidents that never open a session are **not** audit entries; they are
evidence records. The audit log remains a session log.

## 7. Docs

- `SECURITY_MODEL.md` Android sections: replace the unconditional binding claim
  with the contract (excluded from VPN, verified per incident), the state
  table, and the two costs from §2. Remove the "restricted network" language.
- `RATIONALE.md` §4: correct "Android achieves the same goal with
  `bindProcessToNetwork()`" to "only when the VPN does not cover Gatepath".
- `README.md` platform table: "Bind portal traffic to WiFi interface" →
  "Yes, when excluded from the VPN; verified per session".
- `TESTING_ANDROID.md`: add the physical checklist from §8.
- Kotlin comments citing EPERM / restricted networks are deleted, not reworded.

## 8. Testing

- **JVM (`run-jvm-tests.sh` + Gradle).** Table-driven `ConfinementStateTest`
  over bound result × fallback result × VPN kind × Private DNS × host
  resolution; teeth tests that every state is reachable and that `Unknown` is
  not produced by any row that names a state. `ConfinementStateTextTest` for
  the sentence/action table. `IncidentEvidenceTest` field-set guard and a
  redaction test asserting `cert_summary` carries no free-text fields.
  `PortalProbeTest` gains the `probe_path` label. New files are added to
  `MAIN_SOURCES` / `TEST_SOURCES`.
- **Emulator (`tests/e2e-android/`).** Reuse the debug `VpnService`: (a) VPN
  covering the app → assert `Tunnelled`, no WebView request reaches the mock
  gateway; (b) app in the VPN's disallowed list
  (`VpnService.Builder.addDisallowedApplication`) → assert `Confined`, sign-in
  completes, audit entry carries `confinement: confined`. Both are host-side
  assertions in `driver/assertions.py` over pulled artefacts.
- **Physical (manual, documented).** Pixel 9 Pro Fold and Pixel 10 Pro Fold:
  Tailscale tailnet-only, Tailscale exit node, TorGuard, each with Gatepath
  included and excluded; Private DNS strict and automatic; a hostname portal
  and an IP-literal portal. Expected state for each cell recorded in
  `TESTING_ANDROID.md`.
- **CI.** `schema-parity.yml`, `android.yml`, `desktop.yml` unchanged in
  shape; parity covers the v2 bump.

## 9. Out of scope

- Any attempt to bypass the VPN or Private DNS from the app (impossible without
  system permissions; see §1).
- Desktop behaviour changes beyond the audit field.
- Recording non-session incidents in the audit log.
- Launching the system captive-portal handler programmatically
  (`startCaptivePortalApp` is a system API).

## 10. Risks

- TorGuard's split-tunnel implementation may exclude by package but still
  route DNS through the tunnel; the `DnsStrict` and `Tunnelled` branches must
  be distinguished by the Wi-Fi resolver check, not by the VPN kind.
- On Android 15/16 the system handler may hand sign-in to a browser custom
  tab; Gatepath cannot observe that session. The evidence record still covers
  the probe stage.
- Schema v2 readers must not reject v1 files already on users' devices.
