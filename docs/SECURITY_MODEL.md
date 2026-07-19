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

## What Gatepath itself sends

Everything above describes traffic Gatepath *prevents*. This section is the converse:
requests Gatepath originates on your behalf. Neither kind is the portal page.

**1. The connectivity probe.** One HTTP GET to a connectivity-check endpoint
(`connectivitycheck.gstatic.com` on Android, `connectivity-check.ubuntu.com` on desktop)
to decide whether a network is captive. Debug builds may retarget it so the e2e harness
can aim at a mock (`AppModule.resolveProbeUrl`); release builds never do.

**2. The diagnostic battery** (Android only today; the desktop mirror is planned and must
land under this same section). Runs only when a network is flagged captive-suspected and
sign-in is stuck — never on a healthy network. Of the ten probes, five read cached state
and send nothing at all. The other five issue, at most:

- one GET re-running the connectivity check (`HttpProbe`)
- up to 5 GETs following the portal's own redirect chain (`RedirectLoopProbe`)
- one GET re-running the connectivity check, and — only if that one validates — a second
  GET to the HTTPS variant of the probe URL (`HttpsOnlyProbe`)
- one GET whose `Date` response header is compared against the device clock
  (`ClockSkewProbe`)
- one system-resolver lookup of the connectivity-check host, plus **one DNS-over-HTTPS
  query to `1.1.1.1`** naming that same host (`DnsHijackProbe`); the two answers are
  compared to detect a gateway hijacking DNS beyond the probe endpoints

> **The DoH query is the one disclosure to a party that captive-portal detection does not
> already require.** Cloudflare learns your current IP and that you resolved the
> connectivity-check hostname: in practice, that a device at that address is behind a
> captive portal right now. No device, user, SSID, or portal identifier is attached.
>
> It is not Gatepath's only third-party contact — the connectivity probe in §1 above
> reaches Google's `connectivitycheck.gstatic.com`, as the operating system's own captive
> check does. The difference is that the probe is inherent to detecting a portal at all,
> while the DoH query is an additional party contacted for one specific diagnosis. It is
> the price of detecting DNS hijack: the comparison is only meaningful against a resolver
> the captive gateway does not control.
>
> The IP literal (`1.1.1.1`, not `cloudflare-dns.com`) is deliberate: a hostname would be
> resolved by the very resolver under suspicion, and a fully-hijacking gateway would
> answer it with itself, silently blinding the check.

**Routing — diagnostic requests are not bound to the captive network.** Unlike the portal
WebView and the connectivity probe, the diagnostic battery issues its requests on the
device's **default route**. This is causal, not incidental: the battery only runs after the
bound path has already failed, and part of its job is to test whether the *unbound* path
works now (for example, because you just paused your VPN). The consequence is that when
your default route is a VPN tunnel or cellular, these requests — including the DoH query —
travel that path rather than the captive Wi-Fi.

Where that would make a probe's answer meaningless, the probe declines rather than
guessing: if the fallback probe proves the default route reaches the internet without
passing through the captive gateway, `RedirectLoopProbe`, `HttpsOnlyProbe`, and
`DnsHijackProbe` report *inconclusive* and send nothing at all, instead of reporting a
clean result for a network they never touched.

## Android-specific guarantees

- All **portal-session** traffic — the WebView and the connectivity probe — is bound to the
  captive portal `Network` object via `ConnectivityManager.bindProcessToNetwork()`, scoping
  it to the WiFi/Ethernet interface flagged as captive. Gatepath's *diagnostic* requests are
  deliberately unbound and are not portal-session traffic; see
  [What Gatepath itself sends](#what-gatepath-itself-sends) for what they are and why.

> This is proven by an eval, not just asserted: the `tests/e2e-android` no-leak
> sentinel runs a debug-only `VpnService` as the system default network and fails
> the build if the bound portal session's traffic escapes onto it. It passes green
> on the CI emulator (`android-e2e`) and is non-vacuous — a positive control
> confirms the bound WebView actually attempted the sentinel, and the build
> separately proves the apparatus is absent from release builds (ROADMAP P0.1).

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

If you add a vendor here, update `android/app/src/main/java/com/ventouxlabs/gatepath/network/VpnDetector.kt`
and `desktop/gatepath/vpn_detector.py` together.

### Tailscale full-tunnel (exit-node) detection

Beyond interface-name matching, Gatepath queries the Tailscale localapi
`/localapi/v0/status` to distinguish a **full-tunnel** exit node from a plain
split-tunnel Tailscale session. An exit node is active when the status response
carries a nested `ExitNodeStatus` object with a non-empty `ID` (a
`StableNodeID`); that object is omitted when no exit node is selected.

There is **no** top-level `ExitNodeID` field on the `/status` response — that
name belongs to `/localapi/v0/prefs` (the *configured* preference), not the
live status. Reading a top-level `ExitNodeID` from `/status` silently never
matches. Both platforms — `VpnHeuristics.tailscaleBodyIndicatesFullTunnel`
(Android) and `vpn_detector._is_tailscale_full_tunnel` (desktop) — must read
`ExitNodeStatus.ID`, and must fail safe to split-tunnel on any parse error.

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
| Connectivity-check host (Google/Canonical) learning a device is probing | Out (inherent — the OS's own captive check contacts the same endpoint) |
| Third party (Cloudflare) learning a device is behind a captive portal | **Out — accepted cost**: one DoH query per diagnostic run, no identifiers attached; see [What Gatepath itself sends](#what-gatepath-itself-sends) |
| Portal operator observing Gatepath's own diagnostic requests | Out (unavoidable — diagnosing a portal means talking to it) |
| Diagnostic requests travelling the VPN/cellular default route | **In scope — accepted and bounded**: documented above; probes whose answer would be meaningless on that route decline and send nothing. Not covered by the no-leak sentinel, which proves confinement of the *portal session*, not the diagnostic battery |
