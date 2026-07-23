<!-- Generated: 2026-07-23 | Files scanned: ~50 (desktop Python + Rust helper) | Token estimate: ~820 -->

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
| File | Lines | Role |
|------|------:|------|
| `app.py` | 141 | `run_app()` — wires everything, GTK main-loop entry |
| `window.py` | 442 | GTK main window / status UI + diagnosis panel + VPN banner |
| `session_controller.py` | 156 | `SessionController` — arm/close/timeout state machine |
| `session_timer.py` | 111 | Idle/timeout countdown |
| `portal_session.py` | 200 | `PortalSession` value object + lifecycle |
| `portal_monitor.py` | 429 | `Monitor` (polling fallback) + `NMSignalMonitor` (event-driven NM `StateChanged` → re-probe) + `NMCaptiveInterfaceLookup` |
| `portal_launcher.py` | 165 | `PortalLauncher` — detection → `window.open_portal`, GTK-loop marshalled, re-entrancy guarded |
| `portal_probe.py` | 107 | HTTP captive-check probe |
| `netns_client.py` | 401 | D-Bus proxy to the Rust helper; `SetupResult`/`TeardownResult`/`LaunchPortalResult` + `RefusalReason` |
| `portal_webview.py` / `portal_webview_runner.py` | 170/167 | WebKitGTK view, run inside the isolated netns |
| `vpn_detector.py` | 148 | `detect_vpn_interfaces()` — VPN-vs-full-tunnel classification |
| `desktop_isolation.py` | 391 | Isolation backend abstraction (`docs/ISOLATION_BACKENDS.md`) |
| `diagnosis_runner.py` | 98 | Async daemon-threaded diagnostic battery runner |
| `diag_context.py` | 269 | Platform reads for diagnostics — NM + `org.freedesktop.resolve1` D-Bus (DoT detection) |
| `http_fetcher.py` / `no_follow_redirect.py` | 118/38 | urllib fetch + no-redirect handler for probes |
| `blocked_domains.py` | 58 | Tracker-domain block list |
| `audit_log.py` | 121 | Redacted local audit trail (SSID/gateway-IP/portal-domain) |

### `diag/` — pure diagnostics package (no I/O imports; CI-enforced)
`engine.py`, `report.py` (`Cause`), `probe.py` (base) + probes: `dns_hijack`,
`no_dns`, `http`, `https_only`, `http_proxy`, `redirect_loop`, `clock_skew`,
`private_dns`, `vpn`. Probes run over an injected `ProbeContext`; unit-tested
with fakes. Cross-platform cause parity guarded by `test_cause_parity.py`.

## Rust helper (desktop/gatepath-netns-helper/src/)
Runs as root; PolicyKit-authorized on every D-Bus method.
| File | Lines | Role |
|------|------:|------|
| `service.rs` | 2077 | Core orchestration — `GatepathHelperService` |
| `spawn.rs` | 1141 | Privileged exec into the netns (webview, wpa_supplicant, DHCP); portal-URL + display-env validators |
| `connectivity.rs` | 771 | In-netns re-association + DHCP reacquire (was BLOCKER-DESK-002) |
| `netns.rs` | 500 | Named-netns create/teardown, PHY move via `iw` (was BLOCKER-DESK-001) |
| `network_manager.rs` | 499 | NM D-Bus integration — captive re-checks |
| `name_watch.rs` | 372 | D-Bus name-watch → auto-teardown when the caller dies |
| `validation.rs` | 360 | Strict interface-name validation (proptest + cargo-fuzz) |
| `dbus_contract_test.rs` | 352 | Bus-free introspection guard for the D-Bus contract (see above) |
| `audit_log.rs` | 322 | Schema-matching audit entries (root side) |
| `backstop.rs` | 304 | Backstop timer — force-teardown safety net |
| `dbus_service.rs` | 260 | D-Bus method surface + `RefusalReason` error names |
| `throttle.rs` | 235 | Rate-limiting on privileged calls |
| `lib.rs` | 221 | Crate overview / threat-model doc comment |
| `caller_uid.rs` | 159 | Caller UID resolution for auth |
| `auth.rs` | 149 | Authorization checks |
| `policykit.rs` | 110 | PolicyKit integration |

**Trust-boundary validators** (`validate_interface_name`; `validate_portal_url`
/ `validate_wayland_display` / `validate_display` / `validate_xauthority`) are
covered by in-CI `proptest` **and** out-of-CI `cargo-fuzz` targets
(`fuzz/`, nightly-only, its own workspace). `unsafe_code = "deny"`.

Known caveat (`docs/BLOCKERS.md`): secured captive SSIDs (WPA2/EAP) not
supported — open SSIDs only.

## Distribution artifacts
`desktop/gatepath-netns-helper/packaging/` — systemd-sysext image
(`build-sysext.sh` → `.raw`, P2.1), D-Bus/PolicyKit config, tmpfiles.d.
On a `v*` tag these + the Flatpak bundle are cosign-signed and attached to the
Release (P2.3, `release.yml`).
