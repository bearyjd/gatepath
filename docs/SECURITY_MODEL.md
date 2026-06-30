# Gatepath Security Model

Gatepath is a **captive portal handler**. Its job is to give you a controlled, isolated
window for completing a hotel/airport/cafe portal sign-in *without* exposing the rest of
your device's traffic, your VPN tunnel, or your encrypted DNS to the portal operator.

This document is normative. If the code disagrees with this document, the code is wrong.

## What Gatepath protects during a portal session

Gatepath's posture is **isolation by lifecycle**, not **isolation by capability**.
Real captive portals from Meraki, Aruba, Cisco ISE, UniFi, Sky Admin and others
actively rely on cross-host form POSTs, `sessionStorage` / `localStorage` nonces,
and embedded analytics scripts during sign-in — disabling those things makes
the Continue button silently do nothing. Gatepath lets the captive page function
as the operator built it, and instead ensures **nothing it touches survives the
session**.

- The WebView runs with cookies, `sessionStorage`, and `localStorage` **enabled
  during the session** and **wiped on session close**
  (`DisposableEffect.onDispose`):
  `CookieManager.removeAllCookies` + `CookieManager.flush` +
  `WebStorage.deleteAllData` + `clearCache(true)` + `clearFormData` +
  `clearHistory`.
- Off-domain navigations and tracker subresource requests are **observed and
  counted** (audit log) but **allowed to load**. Captive vendors POST sign-in
  forms to backend hosts (e.g. `n143.network-auth.com`) different from the splash
  page, and embed GA/GTM scripts whose `ReferenceError` on `gtag(...)` breaks
  the Continue button. Hard-refusing these caused real-world sign-in failures.
- Cleartext HTTP is permitted **for the captive flow** — portal sign-in pages
  live on local-gateway IPs (RFC1918 / link-local) or on vendor cloud domains
  the captive intercept redirects to; both are unreachable over HTTPS in
  practice. Application code that touches sensitive endpoints stays HTTPS at
  the call site; it does not fall back to cleartext silently.
- Session is time-bounded: auto-closes after 10 minutes.
- Every session is written to an append-only audit log
  (see [AUDIT_LOG_SCHEMA.md](AUDIT_LOG_SCHEMA.md)).

## Android-specific guarantees

- All probe and WebView traffic is bound to the captive portal `Network` object via
  `ConnectivityManager.bindProcessToNetwork()`, scoping it to the WiFi/Ethernet interface
  that NetworkManager flagged as captive.

> An eval for this is implemented (`tests/e2e-android` no-leak sentinel: a
> debug-only `VpnService` runs as the system default network and the build fails
> if any bound-session traffic escapes onto it — ROADMAP P0.1). It is **pending
> its first green CI run**; until then this remains a structural/unit-level
> claim, not an eval-validated one.

- VPN tunnel (Tailscale, TorGuard, WireGuard, OpenVPN) remains active and unmodified
  on all other connections — Gatepath never touches the VPN service.
- Encrypted DNS (Private DNS / NextDNS / DoT / DoH) remains active on all other
  connections — Gatepath does not change system DNS settings.
- Socket binding is enforced in the Android framework's socket-creation path: every
  socket opened by the process while bound is created against the captive `Network`'s
  underlying kernel interface, which the kernel then routes accordingly. The decision
  to bind is user-space (Gatepath calls `bindProcessToNetwork`); the binding cannot
  be bypassed by user-space code in the same process without explicitly using
  `Network.getSocketFactory()` or unbinding via `bindProcessToNetwork(null)`.

### VPN-interface prefixes

When detecting active VPNs to warn about, both platforms enumerate network interfaces
and match these name prefixes. **This list is the source of truth — both platforms
must match it.**

**Common (Android + desktop):** `tun`, `tap`, `wg`, `ipsec`, `ppp`, `tailscale`, `torguard`

**Desktop-only (Linux interface naming for vendor clients):** `proton`, `nordvpn`

**Android-only:** none currently — Android VPN clients use `tun*` exclusively.

If you add a vendor here, update `android/app/src/main/java/cc/grepon/gatepath/network/VpnDetector.kt`
and `desktop/gatepath/vpn_detector.py` together.

### Caveat — `bindProcessToNetwork` is process-wide, not WebView-scoped

The Android API only allows process-wide rebinding. For the duration of an active
portal session, **every socket opened by the Gatepath process** is routed over the
captive WiFi network — including any future feature that issues HTTP. Gatepath does no
other network I/O during a session and the session is capped at 10 minutes, so the
exposure window is small, but new features that issue HTTP from the same process
during a session **must** re-evaluate this guarantee.

The binding is undone in three places to defend against process-death leaks:
1. `DisposableEffect.onDispose` in `GatepathWebView` (graceful close).
2. `Application.onTerminate` (orderly process shutdown).
3. A `ProcessLifecycleOwner` watchdog that fires on whole-app background (debounced
   across in-app activity transitions, so routine pause/resume during navigation does
   NOT yank the binding mid-session).

If the process is killed by the OS without lifecycle callbacks firing, the binding
ends with the process — Android does not persist it across launches.

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

### Native (non-Flatpak) netns helper — status

A privileged helper (`desktop/gatepath-netns-helper/`) is intended to close this
gap for native installs by moving the captive Wi-Fi interface into a dedicated
network namespace and running the portal WebView inside it — the single-host
analogue of Android's `bindProcessToNetwork()` (and of a Qubes DisposableVM).
This would give a kernel-enforced no-leak guarantee even with a full-tunnel VPN
active.

**It is implemented and validated end-to-end on a `mac80211_hwsim` virtual
radio** (the real kernel Wi-Fi stack: nl80211/cfg80211, `wpa_supplicant`, DHCP,
`iw phy … set netns name`). The `tests/e2e-hwsim/` harness proves the full
privileged path and the no-leak invariant: a trusted-net sentinel is
unreachable from inside the netns while the captive portal is reachable —
green and reproducible on real hardware (Bazzite). One caveat remains: only
**open** captive networks are supported (WPA2-PSK/EAP would need credential
capture from NetworkManager); physical-card confirmation (real Wi-Fi
firmware/RF quirks) is pending but is no longer the core unproven risk. See
[`BLOCKERS.md`](BLOCKERS.md) for the remaining confirmation checklist. Until
confirmed on a physical card, treat the isolation guarantee as
hwsim-validated, not production-validated. Deployment of the helper on atomic
distros (e.g. Bazzite) is analysed in
[`DESKTOP_NETNS_DEPLOYMENT.md`](DESKTOP_NETNS_DEPLOYMENT.md).

### Caveat — desktop tracker-resource requests are logged, not blocked

On Android, `WebViewClient.shouldInterceptRequest` lets Gatepath cancel requests to
known tracker domains before they leave the device. On desktop, WebKitGTK's
`resource-load-started` signal is informational — Gatepath observes the request and
increments the counter, but the request still completes. The
`blocked_resource_requests` audit-log field on desktop should be read as
*"observed tracker requests"*, not *"blocked tracker requests"*. See
[AUDIT_LOG_SCHEMA.md](AUDIT_LOG_SCHEMA.md) for the platform-specific semantics.

This is a WebKitGTK API limitation and is honestly disclosed to the user in the
portal-window banner.

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
| Portal page running tracking scripts | **Partial** — allowed (captive vendors embed GA/GTM in splash pages and break on `gtag is not defined`); observed + counted; persistent state wiped on session close |
| Portal page persisting cookies / `sessionStorage` / `localStorage` / cache after session | In — wiped via `CookieManager.removeAllCookies` + `WebStorage.deleteAllData` + `clearCache` |
| Portal page navigating to off-domain phishing pages | **Partial** — allowed (captive vendors POST sign-in forms cross-host); observed + counted; 10-minute session window caps the exposure |
| Operator-network attacker exploiting WebView vulns | Out (mitigated by platform updates) |
| Bug in Gatepath's own state machine leaving session open | In — auto-timeout caps exposure at 10 min |
