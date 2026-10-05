<!-- Updated: 2026-10-04 | Sources: desktop Python, Rust helper and packaging -->

# Backend Codemap — Desktop Python app + Rust privileged helper

## D-Bus contract (Python client ↔ Rust helper)
```
NetnsClient.setup_captive(iface)   → SetupCaptive(s)   → s   | error
NetnsClient.teardown_captive()     → TeardownCaptive() → ()  | error
NetnsClient.launch_portal(ssss)    → LaunchPortal(ssss)→ u   | error
                                    ← PortalSubprocessExited(uii) (signal)
```
Pinned by `docs/netns_helper_dbus_contract.json` + `dbus-contract-parity.yml`:
the **Rust side introspects the real zbus interface with no bus**
(`src/dbus_contract_test.rs` → `Interface::introspect_to_writer` over crate
fakes) and the Python side (`test_dbus_contract.py`) pins client
arities/error-prefix. `netns_client.py:RefusalReason` error names stay in sync
with `dbus_service.rs` via a separate source-parsing guard (`test_netns_client.py`).

## Python app (desktop/gatepath/)
| File | Role |
|------|------|
| `app.py` | `run_app()` — wires everything, GTK main-loop entry |
| `window.py` | GTK main window / status UI + diagnosis panel + VPN banner |
| `session_controller.py` | `SessionController` — arm/close/timeout state machine |
| `session_timer.py` | Idle/timeout countdown |
| `portal_session.py` | `PortalSession` value object + lifecycle |
| `portal_monitor.py` | `Monitor` (polling fallback) + `NMSignalMonitor` (event-driven NM `StateChanged` → re-probe) + `NMCaptiveInterfaceLookup` |
| `portal_launcher.py` | `PortalLauncher` — detection → `window.open_portal`, GTK-loop marshalled, re-entrancy guarded |
| `portal_probe.py` | HTTP captive-check probe |
| `netns_client.py` | D-Bus proxy to the Rust helper; `SetupResult`/`TeardownResult`/`LaunchPortalResult` + `RefusalReason` |
| `portal_webview.py` / `portal_webview_runner.py` | WebKitGTK view, run inside the isolated netns |
| `vpn_detector.py` | `detect_vpn_interfaces()` — VPN-vs-full-tunnel classification |
| `desktop_isolation.py` | Isolation backend abstraction (`docs/ISOLATION_BACKENDS.md`) |
| `diagnosis_runner.py` | Async daemon-threaded diagnostic battery runner |
| `diag_context.py` | Platform reads for diagnostics — NM + `org.freedesktop.resolve1` D-Bus (DoT detection) |
| `http_fetcher.py` / `no_follow_redirect.py` | urllib fetch + no-redirect handler for probes |
| `blocked_domains.py` | Tracker-domain observation list; matches are counted, allowed to load |
| `audit_log.py` | Local audit trail with raw SSID/gateway-IP/portal-domain; support bundles redact identifiers |

### `diag/` — pure diagnostics package (no I/O imports; CI-enforced)
`engine.py`, `report.py` (`Cause`), `probe.py` (base) + probes: `dns_hijack`,
`no_dns`, `http`, `https_only`, `http_proxy`, `redirect_loop`, `clock_skew`,
`private_dns`, `vpn`. Probes run over an injected `ProbeContext`; unit-tested
with fakes. Cross-platform cause parity guarded by `test_cause_parity.py`.
Platform reads/fetches run on the caller's route, separately from the helper's
portal-only namespace.

## Rust helper (desktop/gatepath-netns-helper/src/)
Runs as root; PolicyKit-authorized on every D-Bus method.
| File | Role |
|------|------|
| `service.rs` | Core orchestration — `GatepathHelperService` |
| `spawn.rs` | Privileged exec into the netns (webview, wpa_supplicant, DHCP); portal-URL + display-env validators |
| `connectivity.rs` | In-netns re-association + DHCP reacquire (was BLOCKER-DESK-002) |
| `netns.rs` | Named-netns create/teardown, PHY move via `iw` (was BLOCKER-DESK-001) |
| `network_manager.rs` | NM D-Bus integration — captive re-checks |
| `name_watch.rs` | D-Bus name-watch → auto-teardown when the caller dies |
| `validation.rs` | Strict interface-name validation (proptest + cargo-fuzz) |
| `dbus_contract_test.rs` | Bus-free introspection guard for the D-Bus contract (see above) |
| `audit_log.rs` | Schema-matching audit entries (root side) |
| `backstop.rs` | Backstop timer — force-teardown safety net |
| `dbus_service.rs` | D-Bus method surface + `RefusalReason` error names |
| `throttle.rs` | Rate-limiting on privileged calls |
| `lib.rs` | Crate overview / threat-model doc comment |
| `caller_uid.rs` | Caller UID resolution for auth |
| `auth.rs` | Authorization checks |
| `policykit.rs` | PolicyKit integration |

**Trust-boundary validators** (`validate_interface_name`; `validate_portal_url`
/ `validate_wayland_display` / `validate_display` / `validate_xauthority`) are
covered by in-CI `proptest` and `cargo-fuzz` targets
(`fuzz/`, nightly-only, its own workspace; scheduled `fuzz.yml` soaks outside
PR checks). `unsafe_code = "deny"`.

Known caveat (`docs/BLOCKERS.md`): secured captive SSIDs (WPA2/EAP) not
supported — open SSIDs only.

## Distribution artifacts
`desktop/gatepath-netns-helper/packaging/`:
- `build-rpm.sh` / `gatepath-netns-helper.spec`: complete native app, helper,
  portal runner and assets, with required GUI/network dependencies. Fedora CI
  builds/installs and smoke-tests the installed runtime outside the checkout.
- `build-sysext.sh` / `validate-sysext.sh`: helper, runner wrapper, D-Bus/PolicyKit
  config and tmpfiles.d in a `.raw` extension; host Python app/runtime required
  separately.
- `release.yml` publishes cosign-signed sysext and Flatpak bundles on `v*` tags;
  RPM build/install coverage is CI validation, not an RPM release-publishing job.
Physical-card/open captive AP validation is still pending (#45).
