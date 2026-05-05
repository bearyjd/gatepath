# Gatepath Architecture

Gatepath is a monorepo containing two independent apps that share a security model and
audit-log schema, but no code:

```
gatepath/
├── android/      # Kotlin / Jetpack Compose / Hilt — APK, F-Droid target
├── desktop/      # Python 3.11+ / GTK4 / libadwaita / WebKit2GTK — Flatpak, Flathub target
├── mockportal/   # Shared mock captive portal (Python, stdlib only) — used by tests
└── docs/         # SECURITY_MODEL.md, AUDIT_LOG_SCHEMA.md, ARCHITECTURE.md
```

## High-level flow (both platforms)

```
[ NetworkCallback / NM Connectivity property ]
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
   │  GatepathWebView    │  isolated WebView with off-domain blocking,
   │                     │  cookie-less, ephemeral storage
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

Android's `ConnectivityManager.bindProcessToNetwork(Network)` rebinds **every socket**
opened by the calling process to the given `Network` until cleared. This is enforced in
the kernel, not by user-space configuration. Any HTTP we issue via
`network.openConnection()` and any traffic the WebView emits flows over the WiFi
interface, regardless of the active VPN.

### Desktop — best-effort, user-warned

`SO_BINDTODEVICE` requires `CAP_NET_RAW`; Flatpak does not grant it. We cannot bind
WebKitGTK's sockets to a specific interface. Instead:

1. We read NM's `ConnectivityCheckUri` and `ConnectivityState` so detection works even
   when a VPN is up.
2. We enumerate VPN interfaces (`tailscale0`, `tun*`, `wg*`, `ppp*`) and detect
   exit-node mode for Tailscale.
3. If a full-tunnel VPN is active we show a non-dismissible banner before opening the
   portal window and recommend pausing the VPN.

This is documented honestly to the user in the UI, in [SECURITY_MODEL.md](SECURITY_MODEL.md),
and at portal-window time.

## Data lifetime

- Portal-page data (cookies, cache, localStorage) lives for the session only.
- Audit-log entries persist until the user clears them.
- No telemetry leaves the device.
