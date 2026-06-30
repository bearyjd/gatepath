# Gatepath — Roadmap

This roadmap captures the gaps between **what the project claims** and **what is
actually proven / shippable**, prioritised through an AI-first-engineering lens
(eval coverage over anecdotal confidence; stable, typed contracts that can't
silently drift; deterministic tests; rollout safety).

It is a living planning doc. Items are grouped by leverage toward the product's
core intent, not by effort.

## The intent (the thing every item is measured against)

Gatepath confines captive-portal traffic so it **cannot leak** onto the user's
other connections (VPN, encrypted DNS, normal browsing):

- **Android** — `ConnectivityManager.bindProcessToNetwork()` binds the portal
  WebView to the captive `Network`.
- **Desktop** — a privileged helper moves the Wi-Fi PHY into a throwaway network
  namespace, runs the portal WebView there, and tears it down.

The central observation: the guarantee currently rests on **structural** and
**unit-level** confidence, not on an **eval that proves confinement**. Both e2e
harnesses assert "the portal loads and off-domain requests are blocked" — not
"traffic cannot escape the boundary" — and the desktop privileged path is
exercised only through fakes. Closing that is the highest-value work.

---

## P0 — Evals that test the actual intent

### P0.1 — No-leak sentinel test (both platforms)
**Status:** **proven on both platforms.** Desktop — see P0.2. Android — a
debug-only `VpnService` leak detector + harness + the D1-liveness/D2-confinement
assertion run green end-to-end on the CI emulator (`android-e2e`): an unbound
liveness probe reaches the default route (D1) while the WiFi-bound portal session's
own attempt to reach the sentinel egresses WiFi and never hits the VPN sink (D2),
non-vacuously — a positive control confirms the WebView actually attempted the
sentinel. The VPN-as-default leak-detector mechanism is also confirmed on a
physical Pixel. Release builds provably exclude the apparatus (`release-vpn-guard`).

Stand up a sentinel the confined client **must not** reach, and fail the test if
it does:
- **Desktop** (`tests/e2e-docker` + `tests/e2e-hwsim`): the no-leak invariant is
  **proven end-to-end** — the `tests/e2e-hwsim/` harness drives the **real**
  privileged helper against a `mac80211_hwsim` virtual radio (the real kernel
  Wi-Fi stack: nl80211/cfg80211, `wpa_supplicant`, DHCP, `iw phy set netns`) and
  asserts the trusted-net sentinel is **unreachable** from inside the netns while
  the captive portal IS reachable. Green and reproducible (3/3) on real hardware
  (Bazzite). The docker harness continues exercising the portal flow + off-domain
  blocking (with faked PHY) and is wired into CI (`desktop-e2e.yml`).
- **Android** (`tests/e2e-android`): **proven** — a debug-only `VpnService`
  becomes the default network; the assertion verifies an unbound probe reaches the
  sentinel (D1) and the WiFi-bound portal session's sentinel attempt never reaches
  the VPN sink (D2), with a positive control that the WebView actually attempted
  it. Green on the CI emulator; release builds provably exclude the apparatus
  (`release-vpn-guard`). (The literal claim in `SECURITY_MODEL.md`.)

### P0.2 — Virtual-radio integration harness (`mac80211_hwsim` + `hostapd`)
**Status:** **done — validated end-to-end on a `mac80211_hwsim` virtual radio.**
The `tests/e2e-hwsim/` harness proves the full privileged path and the no-leak
invariant: PHY move into a throwaway `gatepath` netns → in-netns `wpa_supplicant`
re-association → DHCP → portal WebView runner → teardown. The trusted-net
sentinel is UNREACHABLE from inside the netns; the captive portal IS reachable.
Green and reproducible (3/3) on real hardware (Bazzite). `mac80211_hwsim` is a
real mac80211 driver exercising the real kernel Wi-Fi stack (nl80211/cfg80211) —
not a fake. This resolves BLOCKER-DESK-003's software-validation gate and unblocks
P0.1's desktop confinement gate.

---

## P1 — Stable contracts (no hidden drift)

### P1.1 — `UnsupportedSecurity` contract drift + drift guard
**Status:** core fix landed (#51); follow-ups below. **Why it matters:** a
present bug on a path we built.

The Rust helper could emit `RefusalReason::UnsupportedSecurity` (secured captive
network refused pre-setup), but the Python `RefusalReason` enum and
`from_dbus_error_name` mapping (`desktop/gatepath/netns_client.py`) omitted it,
so the UI saw `UNKNOWN`. #51 fixed the mapping and added a cross-language drift
guard (`test_python_refusal_reasons_cover_every_rust_variant`) that parses
`RefusalReason::as_str()` out of the Rust `lib.rs`.

**Guard refinements (from the devils-advocate review of #51):**
- **Round-trip through `from_dbus_error_name`** instead of asserting enum-value
  membership — the current guard would miss "enum value present but mapping entry
  absent" (the half that actually broke) and wrong-mapping. Highest value.
- Add a **negative test** (an unknown suffix must resolve to `UNKNOWN`) so the
  guard can't silently go vacuous.
- Fix the guard's **over-claim**: it covers *RefusalReason* wire names, not
  literally "every wire error" — `HelperError` (`dbus_service.rs`) also carries
  `NotActive`/`ZBus`, which aren't `RefusalReason`s (`NotActive`'s mapping is
  pinned in `test_refusal_reason_maps_known_variants`).
- Optionally **parse `HelperError`** (the real wire-error enum) for full wire
  coverage incl. `NotActive`.
- Polish: name the likely cause in the parse-failure message, document the
  PascalCase↔snake 1:1 convention the reconstruction relies on, and
  cross-reference `schema-parity.yml` as the heavier shared-artifact alternative.

**Bigger drift guard (still open):** extend the `schema-parity.yml` pattern
(shared artifact + per-language conformance tests) to the **D-Bus interface +
error names** — a checked introspection XML / shared error-name list both sides
validate against — and move the `LaunchPortal` arity pin and the
`PortalSubprocessExited` signal-shape check out of `--ignored` (they never run in
CI today).

### P1.2 — Property/fuzz the privileged boundary validators
**Status:** not started. **Why it matters:** these validators *are* the trust
boundary; example-only tests can miss what an agent-introduced refactor breaks.

Add `proptest` / `cargo-fuzz` targets for `validate_portal_url`,
`validate_interface_name`, and the DESK-004 display validators
(`validate_wayland_display` / `validate_display` / `validate_xauthority`).

---

## P2 — Rollout safety (the intent isn't met if users can't run it)

### P2.1 — A buildable helper package
**Status:** not started. `DESKTOP_NETNS_DEPLOYMENT.md` *analyses* sysext vs RPM
but ships **no artifact** (no `.spec`, no sysext build, no `install.sh`); the
Flatpak contains the GUI only. Produce one (sysext fits the atomic-distro target)
plus a CI job that builds it.

### P2.2 — Android release pipeline
**Status:** not started. Debug/unsigned only — no signing config, no AAB, no
F-Droid metadata (the natural channel for a privacy tool), no release/tag
automation.

### P2.3 — Supply-chain hardening
**Status:** not started. `cargo-audit` exists; add `pip-audit` for the Python
side, a `dependabot`/`renovate` config, signed releases, and an SBOM.

---

## P3 — Operability

### P3.1 — Field diagnostics / troubleshooting
**Status:** not started. The Android diagnostic engine and JSONL audit logs
exist, but there's no "export diagnostics" path and no `TROUBLESHOOTING.md` for
operators deploying the desktop helper.

---

## Known limitations (intentional, tracked elsewhere)

- **Open captive networks only.** Secured (WPA2-PSK/EAP) re-association needs the
  PSK/credentials from NetworkManager's secret store — a separate,
  security-sensitive piece of work. Scaffolded (`WifiSecurity::Psk` +
  `ConnectivityError::Unsupported`) but not implemented.
- **Desktop path validated on a `mac80211_hwsim` virtual radio** (real kernel
  Wi-Fi stack); physical-card confirmation (real Wi-Fi firmware/RF quirks) is
  pending but is no longer the core unproven risk — the privileged path and
  no-leak invariant are proven. **Open captive networks only.**

---

## What's already solid (so this roadmap is calibrated, not alarmist)

- Strong unit coverage (Rust + pytest + Android JVM) with pinned privileged argv.
- **Two** real e2e harnesses (Android emulator, desktop Docker) that prove the
  portal flow + off-domain blocking.
- `schema-parity.yml` CI for the audit-log schema across desktop/Android.
- `cargo-audit` in CI; an `unsafe`-free privileged crate (`unsafe_code = "deny"`).
- Unusually honest docs and blocker tracking (`BLOCKERS.md`, `SECURITY_MODEL.md`).

The gaps above are about **proving the core invariant** and **shipping** — not
cleanup.
