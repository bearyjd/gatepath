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
**Status:** not started. **Why it matters:** this is *the* eval for the product;
neither e2e harness has it today.

Stand up a sentinel the confined client **must not** reach, and fail the test if
it does:
- **Desktop** (`tests/e2e-docker`): a listener in the host netns / on a second
  veth that the in-netns WebView must not connect to.
- **Android** (`tests/e2e-android`): a VPN (or second network) active + a server
  reachable only off the captive `Network`; must not be hit. (This is the literal
  claim in `SECURITY_MODEL.md`.)

### P0.2 — Virtual-radio integration harness (`mac80211_hwsim` + `hostapd`)
**Status:** not started. **Why it matters:** converts BLOCKER-DESK-003 from
"pending physical hardware" into "validated in a software harness" — the single
biggest lever on the desktop guarantee, and it's pure software.

Run the **real** privileged path — `iw phy set netns`, in-netns `wpa_supplicant`,
DHCP — in CI / a VM with no physical card, using `mac80211_hwsim` (+ `hostapd`
for an open AP, optionally `wmediumd`). Today `tests/e2e-docker` uses veth +
static IP and skips the PHY move, supplicant, and DHCP entirely; the `--ignored`
integration suite is the intended home for the on-hardware checks.

---

## P1 — Stable contracts (no hidden drift)

### P1.1 — Fix `UnsupportedSecurity` contract drift + add a drift guard
**Status:** in progress. **Why it matters:** a present bug on a path we built.

The Rust helper can emit `RefusalReason::UnsupportedSecurity` (secured captive
network refused pre-setup), but the Python `RefusalReason` enum and
`from_dbus_error_name` mapping (`desktop/gatepath/netns_client.py`) omit it — so
the UI sees `UNKNOWN` instead of the typed reason. Fix the mapping, then close
the class: extend the existing `schema-parity.yml` pattern (already used for the
audit-log schema) to the **D-Bus interface + error names**, so a checked
introspection XML / shared error-name list catches Rust↔Python drift in CI. Also
move the `LaunchPortal` arity pin and the `PortalSubprocessExited` signal-shape
check out of `--ignored` (they never run in CI today).

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
