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
**Status:** desktop sentinel + harness modernization landed; **full confinement
gate blocked on P0.2**. Android: not started.

Stand up a sentinel the confined client **must not** reach, and fail the test if
it does:
- **Desktop** (`tests/e2e-docker`): implemented — a `trusted-sentinel` on a
  separate `trusted_net`; the scenario asserts it's reachable from the host netns
  (`sentinel_baseline`) and the in-netns runner probes it (`netns_confinement`,
  must FAIL). **But** tackling this surfaced that the docker e2e had **silently
  rotted since DESK-001/002** (not in CI): the helper now does a PHY move +
  in-netns `wpa_supplicant`/DHCP that a **veth has no PHY/radio for**. Fixed the
  drift that *can* be faked (dbusmock NM AP-state, `wpa_supplicant`/DHCP stubs)
  and made the scenario record an explicit `privileged_path: skipped` on a veth
  so it goes **green** up to the PHY move; the confinement gate runs unchanged
  once a real radio exists. Also **wired the harness into CI** (`desktop-e2e.yml`)
  so it can't rot silently again.
- **Android** (`tests/e2e-android`): not started — a VPN (or second network)
  active + a server reachable only off the captive `Network`; must not be hit.
  (The literal claim in `SECURITY_MODEL.md`.)

### P0.2 — Virtual-radio integration harness (`mac80211_hwsim` + `hostapd`)
**Status:** not started. **Why it matters:** converts BLOCKER-DESK-003 from
"pending physical hardware" into "validated in a software harness" — the single
biggest lever on the desktop guarantee, and it's pure software. **Also unblocks
P0.1's desktop confinement gate** (a veth can't run the PHY move / supplicant).

Give `tests/e2e-docker` a real Wi-Fi PHY: `mac80211_hwsim` (+ `hostapd` for an
open AP, optionally `wmediumd`) instead of the veth. The scenario already runs
the full privileged path + the no-leak confinement gate the moment
`/sys/class/net/wlan0/phy80211` exists — so this is mostly a substrate swap in
the client container + verifying the runner can load `mac80211_hwsim`.

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
- **Desktop path is unvalidated on real Wi-Fi hardware** (BLOCKER-DESK-003 / #45).
  P0.2 is the software path to closing this without a physical card.

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
