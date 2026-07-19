# Diagnostics Expansion — Design

**Date:** 2026-07-18
**Status:** Approved (design); implementation planned separately
**Scope:** Android + Desktop

## Problem

When a captive portal fails to open or complete, users get little help figuring
out why. Android has a diagnostics engine (`android/.../diag/`) that models
9 failure causes but only runs 2 probes (`PrivateDnsProbe`, `HttpProbe`), runs
only automatically on `CaptivePortalSuspected`, and shows only the single top
cause. Desktop has no in-app diagnostics UI at all — only a VPN banner and an
offline sudo script (`collect-diagnostics.sh`).

## Goals

- Users on both platforms can answer "why isn't this portal working?" in-app.
- Diagnostics run automatically on portal-suspected **and** on demand via a
  "Run diagnostics" button; per-probe results are visible, not just the top cause.
- The cause vocabulary is shared across platforms and drift-guarded by a test,
  per repo convention (precedent: `test_python_refusal_reasons_cover_every_rust_variant`).

## Non-goals

- Auto-applying fixes (existing design decision D1 stands: actions are
  instructions + settings intents only).
- `SandboxedWebView` probe (depends on the unbuilt Phase-3.5 WebView bridge).
- Secured-Wi-Fi support changes (see `docs/BLOCKERS.md`).

## Cause vocabulary (shared)

Existing Android variants: `Healthy`, `VpnBlocking`, `DnsHijack`,
`PrivateDnsBlocking`, `HttpProxyBlocking`, `SandboxedWebView`,
`HttpsOnlyCaptive`, `CellularFallback`, `Inconclusive`.

New variants (both platforms): `NoDnsServers` (broken DHCP),
`PortalRedirectLoop`, `ClockSkew` (device clock wrong; breaks TLS to portal).

Severity ranking remains centralized: Android `DiagnosticEngine.rankOf` gains
`NoDnsServers=85`, `PortalRedirectLoop=65`, `ClockSkew=55`; desktop mirrors the
full table. Desktop legitimately lacks `CellularFallback`, `SandboxedWebView`,
and `PrivateDnsBlocking` (Android-only concepts); the parity guard encodes this
allowlist explicitly.

## Android

### New probes (one file each in `diag/`, pure JVM, no `android.*`)

| Probe | Emits | Mechanism |
|---|---|---|
| `VpnProbe` | `VpnBlocking` | Context-only: `ctx.vpnInterfaces` non-empty and/or `ctx.isTailscaleFullTunnel` while portal unresolved. |
| `HttpProxyProbe` | `HttpProxyBlocking` | Context-only: `ctx.httpProxyDescription` non-null. |
| `NoDnsProbe` | `NoDnsServers` | Context-only: `ctx.dnsServerCount == 0`. |
| `CellularFallbackProbe` | `CellularFallback` | Context-only: new `ctx.hasValidatedCellular` field (populated in `MainViewModel` from ConnectivityManager; `ProbeContext` stays pure). |
| `RedirectLoopProbe` | `PortalRedirectLoop` | Follows the portal redirect chain over the bound network, max 5 hops; revisited URL ⇒ loop. |
| `HttpsOnlyProbe` | `HttpsOnlyCaptive` | HTTP probe validated/silent but an HTTPS fetch to the connectivity host is intercepted or reset (the deferred "Phase 4" fan-out in `HttpProbe`'s doc). |
| `ClockSkewProbe` | `ClockSkew` | Compares the `Date` response header from the existing probe response with device clock; flags skew > 5 minutes. No extra request. |
| `DnsHijackProbe` | `DnsHijack` | Resolves the connectivity-check host via system DNS and via DoH (`https://1.1.1.1/dns-query`, bound network); RFC1918/gateway mismatch while portal claims resolved ⇒ hijack. |

All probes register in `DiagnosticModule` (the single membership point). New
`RecommendedAction` ids: `OPEN_DATE_TIME_SETTINGS` (ClockSkew),
`RECONNECT_NETWORK` (NoDnsServers, PortalRedirectLoop); `DiagnosisPanel.intentFor`
gains the matching Settings intents. Probe failures degrade to `Inconclusive`
with the raw message under the existing 5s-total / 2s-per-probe budgets.

### UI

- **"Run diagnostics" button** on the troubleshooting panel in `MainScreen`:
  re-snapshots `NetworkDiagnostics`, rebuilds `ProbeContext`, re-runs the
  engine. Auto-run on `CaptivePortalSuspected` is unchanged.
- **"All checks" section** in `DiagnosisPanel`: collapsible per-probe rows
  (name, pass/fail/inconclusive, one-line detail) rendered from the engine's
  full ranked report list (already produced; today only the top is shown).
  The panel must not re-rank — ordering comes from the engine.
- Share-diagnostics bundle picks the new rows up automatically via
  `DiagnosisResult` rendering; redaction contract unchanged.

## Desktop

### New `desktop/gatepath/diag/` package (pure, stdlib-only, pytest-able)

Mirror of the Android shape:

- `report.py` — cause enum + report/result dataclasses (immutable).
- `probe.py` — probe protocol + `ProbeContext` dataclass.
- `engine.py` — runs probes concurrently with 5s total / 2s per-probe budgets,
  same severity table, maps top cause to a recommended action.
- Probes: `vpn_probe` (wraps `vpn_detector`), `proxy_probe`
  (`http(s)_proxy` env + GNOME proxy settings), `no_dns_probe`
  (resolv.conf / NetworkManager DNS list), `redirect_loop_probe`,
  `https_only_probe`, `clock_skew_probe`, `dns_hijack_probe`
  (`getaddrinfo` vs DoH). One concern per file.

### UI

New `ui/diagnosis_panel.blp` + `diagnosis_panel.py`: "Most likely cause"
headline + recommended-action text, expandable per-probe rows, "Run
diagnostics" button. Shown by `window.py` on suspected portal (augmenting the
VPN banner) and runnable manually anytime. Engine runs off the GTK main loop
(worker thread + `GLib.idle_add`, matching existing app patterns). All errors
surface as an inconclusive row, never a crash or silent omission.

## Testing

- TDD throughout. Android probes are SDK-free ⇒ covered by
  `run-jvm-tests.sh` / `./gradlew :app:test`. Desktop via pytest with fake
  contexts.
- `mockportal/server.py` gains a redirect-loop endpoint and a skewed-`Date`
  response mode so both platforms test network probes against the same fixture
  (loopback-only default preserved).
- New e2e coverage follows the assertions-are-a-separate-pass rule: host-side
  checks in `driver/assertions.py`, not scenario steps.
- **Parity guard:** a test parses the Kotlin sealed `DiagnosticReport` variants
  from source and asserts the Python cause enum covers every variant except the
  explicit Android-only allowlist (and vice versa for desktop-only causes, if
  any).

## PR sequencing

1. **Android context-only probes** (`VpnProbe`, `HttpProxyProbe`, `NoDnsProbe`,
   `CellularFallbackProbe`) + new variants/ranks/actions + "Run diagnostics"
   button + all-checks list.
2. **Android network probes** (`RedirectLoopProbe`, `HttpsOnlyProbe`,
   `ClockSkewProbe`, `DnsHijackProbe`) + mockportal fixture additions.
3. **Desktop `diag/` package** + probes + tests (no UI).
4. **Desktop GTK diagnosis panel** + run button + wiring.
5. **Cause-parity drift guard + docs** (`TROUBLESHOOTING.md`, roadmap note).

Each PR lands via review per repo convention; no direct pushes to `main`.
