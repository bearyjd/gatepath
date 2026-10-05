# Gatepath Architecture

Gatepath is a monorepo containing two independent apps that share a security model and
audit-log schema, but no code:

```
gatepath/
├── android/      # Kotlin / Jetpack Compose / Hilt — APK, F-Droid target
├── desktop/      # Python 3.11+ / GTK4 / libadwaita / WebKitGTK 6.0 — native RPM + Flatpak
├── mockportal/   # Shared mock captive portal (Python, stdlib only) — used by tests
└── docs/         # SECURITY_MODEL.md, AUDIT_LOG_SCHEMA.md, ARCHITECTURE.md
```

## High-level flow (both platforms)

```
[ NetworkCallback / NM Ip4Connectivity property ]
              │
              ▼
   ┌─────────────────────┐
   │ CaptivePortalMonitor│  emits portal_detected with Network/connection ref
   └─────────────────────┘
              │
              ▼
   ┌─────────────────────┐
   │  PortalSession      │  state machine: Idle → Monitoring → Detected → Active → Completed
   └─────────────────────┘
              │
              ▼
   ┌─────────────────────┐
   │  Portal WebView    │  off-domain/tracker observations, allowed to load;
   │                     │  cookies/storage enabled, wiped on session close
   └─────────────────────┘
              │
              ▼
   ┌─────────────────────┐
   │     AuditLog        │  append-only JSONL — see AUDIT_LOG_SCHEMA.md
   └─────────────────────┘
```

## Why two independent apps and not KMP/Compose Multiplatform?

The interesting code in Gatepath is the platform integration: NetworkCallback,
`bindProcessToNetwork`, NetworkManager D-Bus, WebKit2GTK policy decisions. Sharing a
core library would buy us almost nothing while making both apps harder to package
through their respective stores (Play / F-Droid / Flathub). The shared contract is the
audit-log schema, which is plain JSONL.

## Network isolation, by platform

### Android — kernel-enforced

Android uses the captive `Network` for probes and holds a process-binding lease
for portal browsing. Confinement classification gates normal sign-in: failed or
unknown confinement displays recovery actions instead of opening the WebView.
The system handoff in `CaptivePortalActivity` handles fold/rotation configuration
changes in place, preserving its lease, classification and page. A successful
classification is required; a binding attempt alone is not proof of confinement.

`MainViewModel` coordinates sessions. `SessionIncidentState` latches accepted
incident attribution and resets it on timeout, dismissal, successful sign-in,
network close and debug-force activation. `GatepathWebView` refreshes its client
when the portal host changes so host-specific navigation/TLS policy stays current.

### Desktop — native namespace isolation or unconfined fallback

The privileged Rust helper moves the captive Wi-Fi PHY into a network namespace,
re-associates to an open SSID, obtains DHCP and launches the fixed native WebKit
runner as the caller's user inside that namespace. The native RPM includes the
Python app, runner, helper and required GTK/WebKit dependencies. The sysext ships
the helper and wrapper, requiring the host app/runtime separately. Flatpak can
call an installed host helper using the development manifest's D-Bus grant.

The namespace no-leak path has virtual-radio (`mac80211_hwsim`) coverage;
physical Wi-Fi adapter/open captive AP confirmation remains pending (#45).
Secured SSIDs are unsupported by this path. Desktop diagnostics use the caller's
normal route; the helper isolates portal browsing.

If the helper is unavailable, fallback browsing uses the host route:

1. We read NM's captive connectivity state and configured probe URL.
2. We enumerate VPN interfaces (`tailscale0`, `tun*`, `wg*`, `ppp*`) and detect
   exit-node mode for Tailscale.
3. If a full-tunnel VPN is active we show a non-dismissible banner before opening the
   portal window and recommend pausing the VPN.

This is documented honestly to the user in the UI, in [SECURITY_MODEL.md](SECURITY_MODEL.md),
and at portal-window time.
Fallback sessions are audited as `unconfined`; installing the complete RPM does
not turn helper failure into a fail-closed policy.

## Data lifetime

- Portal-page data (cookies, cache, localStorage) lives for the session only.
- Audit-log entries persist until the user clears them.
- No telemetry leaves the device.
