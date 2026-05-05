# Gatepath Security Model

Gatepath is a **captive portal handler**. Its job is to give you a controlled, isolated
window for completing a hotel/airport/cafe portal sign-in *without* exposing the rest of
your device's traffic, your VPN tunnel, or your encrypted DNS to the portal operator.

This document is normative. If the code disagrees with this document, the code is wrong.

## What Gatepath protects during a portal session

- WebView/portal window navigation is restricted to the portal's origin domain only.
- All third-party resource requests from the portal page are blocked and logged.
- No portal page data (cookies, cache, localStorage) persists after session close.
- Session is time-bounded: auto-closes after 10 minutes.
- Every session is written to an append-only audit log
  (see [AUDIT_LOG_SCHEMA.md](AUDIT_LOG_SCHEMA.md)).

## Android-specific guarantees

- All probe and WebView traffic is bound to the captive portal `Network` object via
  `ConnectivityManager.bindProcessToNetwork()`, scoping it to the WiFi/Ethernet interface
  that NetworkManager flagged as captive.
- VPN tunnel (Tailscale, TorGuard, WireGuard, OpenVPN) remains active and unmodified
  on all other connections — Gatepath never touches the VPN service.
- Encrypted DNS (Private DNS / NextDNS / DoT / DoH) remains active on all other
  connections — Gatepath does not change system DNS settings.
- Socket binding is enforced at the kernel level via `Network.openConnection()`; no
  configuration error in user space can leak portal traffic into the VPN tunnel.

## Desktop-specific limitations (be explicit)

- Linux's `SO_BINDTODEVICE` requires `CAP_NET_RAW`, which is unavailable in the Flatpak
  sandbox by design.
- Gatepath **CANNOT** guarantee that WebKitGTK traffic routes via the WiFi interface
  rather than through an active full-tunnel VPN.
- If TorGuard, Mullvad, or a Tailscale exit-node is active, the portal page may not load
  at all (the VPN's far end probably can't reach the portal's local-network gateway).
- **Mitigation:** Gatepath detects active VPN interfaces and warns the user before
  opening the portal window.
- **Mitigation:** Gatepath reads the portal URL from NetworkManager (which probes
  independently of the routing table) rather than doing its own probe, so detection
  works even when the VPN is up.
- **Recommendation:** pause full-tunnel VPN before navigating a portal on desktop.
  Gatepath will remind you of this.

## What neither platform protects against

- The portal operator can see your device's traffic during the portal session. This is
  unavoidable — it's the mechanism. Authenticating to a portal means revealing yourself
  to it.
- Portal pages may fingerprint your device via browser APIs (canvas, WebGL where
  available, navigator properties). We disable some, but the surface is large.
- Gatepath does not verify the portal operator's identity. A malicious actor on the
  same network can impersonate the portal.
- Gatepath does not protect against vulnerabilities in WebKitGTK or the Android
  System WebView. Keep your platform up to date.

## Threat model summary

| Adversary | In scope? |
|---|---|
| Portal operator capturing portal-window traffic | **Out** (unavoidable) |
| Portal operator capturing your VPN/DNS traffic on Android | In — prevented |
| Portal operator capturing your VPN/DNS traffic on desktop | **Partial** — warned, not prevented |
| Portal page running tracking scripts | In — blocked + logged |
| Portal page persisting cookies/cache after session | In — wiped |
| Portal page navigating to off-domain phishing pages | In — refused |
| Operator-network attacker exploiting WebView vulns | Out (mitigated by platform updates) |
| Bug in Gatepath's own state machine leaving session open | In — auto-timeout caps exposure at 10 min |
