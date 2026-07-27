# Changelog

All notable changes to Gatepath are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project aims to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Gatepath is **two independent apps** — `android/` (Kotlin/Compose) and `desktop/`
(Python/GTK4 + a privileged Rust netns helper) — sharing a security model and an
audit-log schema; entries tag the platform where it isn't obvious. **No versioned
release has been cut yet**, so everything below is unreleased, heading toward
1.0.0. The living, detailed status lives in [`docs/ROADMAP.md`](docs/ROADMAP.md).

## [Unreleased]

### Added

- **Captive-portal confinement — the core capability.** Android confines the
  sign-in flow with `VpnService`-based leak detection (no root); the Linux desktop
  moves the Wi-Fi interface into a dedicated **network namespace** via a
  privileged, PolicyKit-gated Rust D-Bus helper (`gatepath-netns-helper`) and runs
  the sign-in WebView confined to it — so the captive negotiation can't see or leak
  the user's normal traffic, VPN, or private DNS.
- **On-device audit log** of portal sessions (never page contents or credentials),
  with a single cross-platform schema (`docs/audit_log_schema.json`) enforced on
  both platforms; redaction of SSID / gateway IP / portal domain.
- **Diagnostics battery** on both platforms — a shared ~12-cause set (DNS hijack,
  HTTPS-only portal, redirect loop, clock skew, HTTP proxy, VPN full-tunnel, and
  strict private DNS / DNS-over-TLS) with recommended fixes. Android shows results
  automatically; the desktop app has a "Run diagnostics" panel.
- **Desktop live portal detection** — event-driven NetworkManager `StateChanged`
  signal monitoring drives detection → confined portal launch (polling fallback).
- **Android "Share Diagnostics"** — a redaction-by-default support bundle
  (`ACTION_SEND`) via a non-exported `FileProvider`.
- **Packaging for the desktop helper:** a `systemd-sysext` image for
  immutable/atomic distros, **and an RPM `.spec`** (`packaging/gatepath-netns-helper.spec`)
  for traditional Fedora/RHEL — same canonical `/usr` layout, built and layout-checked
  in CI (Fedora container). (#107)
- **Supply-chain provenance:** every release artifact — the Android AAB/APK, the
  SBOM, and the desktop sysext `.raw` + Flatpak bundle — is signed with **keyless
  cosign** (Sigstore OIDC → Fulcio → Rekor, no long-lived keys); verify recipe in
  `docs/RELEASING.md §4`. (#97, #101)
- **Trust-boundary test coverage:** property tests (`proptest`) *and* coverage-guided
  **`cargo-fuzz`** targets for the five privileged-boundary validators, plus a
  **scheduled nightly fuzz soak** with a persisted corpus. (#103, #108)
- **Cross-language drift guards** (machine-checked, not commented): audit-log
  schema parity, the D-Bus method/signal contract (`docs/netns_helper_dbus_contract.json`),
  D-Bus refusal-reason names + error prefix, and diagnosis cause parity. (#96, #99)
- **`CHANGELOG.md`** (this file).

### Changed

- The tag-triggered release workflow now builds, signs, and attaches the **desktop**
  artifacts alongside the Android ones, with `id-token`/`contents` permissions
  scoped per-job (least privilege) and a build-only, token-free Flatpak container. (#101)
- Documentation refreshed to the two-app + Rust-helper reality: `CONTRIBUTING.md`
  rewritten, `docs/CODEMAPS/` re-synced, and `docs/ROADMAP.md` / `docs/BLOCKERS.md`
  kept current. (#104, #105, #106)

### Fixed

- `release.yml` flatpak-release job could not `gh release upload` (no repo
  context in an artifact-only job); pass `GH_REPO`. Caught by a live test-tag run. (#102)
- `nightly-fuzz` pinned to a known-good nightly after the always-latest nightly
  `rustc` hit an internal compiler error under cargo-fuzz's sanitizer flags. (#109)
- **Android:** captive portals served over HTTPS with a self-signed, expired, or
  IP-CN certificate rendered as a blank white screen. The WebView had no
  `onReceivedSslError` override, so the Android default (`handler.cancel()`)
  aborted the load with no error callback and nothing logged. (#111)

- **Desktop:** off-domain navigations were refused outright, which cancels the
  cross-host sign-in POST that Meraki, Cisco ISE and UniFi portals rely on —
  the user presses Continue and nothing happens. They are now observed and
  counted but allowed to load, matching Android and the behaviour the shared
  docs already described. Host matching is also subdomain-aware and no longer
  compares ports, so a sub-host of the portal is no longer treated as
  off-domain. (#115)

### Security

- Confinement (netns on desktop, `VpnService` on Android) is the product's core
  security property; the threat model is documented in `docs/SECURITY_MODEL.md`.
- The Rust helper is `unsafe`-free (`unsafe_code = "deny"`) and PolicyKit-authorizes
  every privileged D-Bus call; its input validators are proptest- and fuzz-covered.
- Release artifacts carry cosign provenance independent of the (optional) Android
  keystore app signature.
- **Android:** the captive-portal TLS-error bypass is scoped to the portal host
  and its subdomains (`SslErrorPolicy`); certificate errors on any other host are
  cancelled, so a hostile gateway cannot redirect the session to an arbitrary
  host and MITM it with an untrusted certificate. Fails closed when either host
  cannot be parsed. (#111)
- Bypassed certificate errors are recorded in the audit log as
  `tls_cert_errors_bypassed`, so a session that rendered a page with an invalid
  certificate leaves evidence. Added as an `optional_fields` entry in the shared
  schema — no `schema_version` bump, and pre-existing log lines stay valid.
  Always `0` on desktop, which has no TLS-error handler.

### Known limitations

- Secured captive networks (WPA2-PSK / EAP) are **not** supported — open SSIDs only.
- Desktop DoH-forwarder detection is intentionally not implemented (no D-Bus/portal
  signal exists). See `docs/BLOCKERS.md`.

[Unreleased]: https://github.com/bearyjd/gatepath/commits/main
