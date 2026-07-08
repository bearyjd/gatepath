<!-- Generated: 2026-07-05 | Files scanned: 30 (desktop Python + Rust helper) | Token estimate: ~700 -->

# Backend Codemap — Desktop Python app + Rust privileged helper

## D-Bus contract (Python client ↔ Rust helper)
```
NetnsClient.setup_captive(iface)   → SetupCaptive(interface_name)   → SetupSuccess | SetupRefused
NetnsClient.teardown_captive()     → TeardownCaptive()              → TeardownSuccess | TeardownRefused
NetnsClient.launch_portal(...)     → LaunchPortal(...)              → LaunchPortalSuccess | LaunchPortalRefused
                                    ← portal_subprocess_exited (signal) → SubprocessExit
```
`netns_client.py:RefusalReason` enum parses D-Bus error names emitted by
`dbus_service.rs` — kept in sync via the P1.1 drift-guard test (PR #57).

## Python app (desktop/gatepath/)
| File | Role |
|------|------|
| `app.py` (98) | `run_app()` — wires everything together, GTK main loop entry |
| `window.py` (243) | GTK main window / status UI |
| `session_controller.py` (156) | `SessionController` — arm/close/timeout state machine over a `PortalSession` |
| `session_timer.py` (111) | Idle/timeout countdown used by SessionController |
| `portal_session.py` (200) | `PortalSession` value object + lifecycle |
| `portal_monitor.py` (209) | Polls NetworkManager D-Bus for captive-portal device state (`Monitor`, `NMCaptiveInterfaceLookup`) |
| `portal_probe.py` (125) | HTTP captive-check probe |
| `netns_client.py` (401) | D-Bus proxy to the Rust helper; `SetupResult`/`TeardownResult`/`LaunchPortalResult` types |
| `portal_webview.py` / `portal_webview_runner.py` (170/167) | WebKitGTK view, run inside the isolated netns by the helper's spawn path |
| `vpn_detector.py` (103) | `detect_vpn_interfaces()` — VPN-vs-full-tunnel classification before isolating |
| `desktop_isolation.py` (391) | Isolation backend abstraction (see `docs/ISOLATION_BACKENDS.md`) |
| `blocked_domains.py` (58) | Tracker-domain block list |
| `audit_log.py` (121) | Redacted local audit trail (SSID/gateway-IP/portal-domain redaction) |

## Rust helper (desktop/gatepath-netns-helper/src/)
Runs as root; PolicyKit-authorized on every D-Bus method.
| File | Lines | Role |
|------|------:|------|
| `service.rs` | 2077 | Core orchestration — largest module, houses `GatepathHelperService` |
| `spawn.rs` | 1141 | Privileged exec into the netns (webview subprocess, wpa_supplicant, DHCP) |
| `connectivity.rs` | 771 | In-netns re-association + DHCP reacquire (was BLOCKER-DESK-002, now implemented) |
| `netns.rs` | 500 | Named-netns create/teardown, PHY move via `iw phy … set netns` (was BLOCKER-DESK-001) |
| `network_manager.rs` | 499 | NM D-Bus integration — captive re-checks |
| `name_watch.rs` | 372 | D-Bus name-watch → auto-teardown when the unprivileged app dies |
| `validation.rs` | 360 | Strict interface-name / input validation (property-tested, P1.2 PR #58) |
| `audit_log.rs` | 322 | Schema-matching audit entries (root side) |
| `backstop.rs` | 304 | Backstop timer — force-teardown safety net |
| `dbus_service.rs` | 260 | `DbusService` — D-Bus method surface: `setup_captive`, `teardown_captive`, `launch_portal`, `portal_subprocess_exited` |
| `throttle.rs` | 235 | Rate-limiting on privileged calls |
| `lib.rs` | 216 | Crate overview / threat-model doc comment |
| `caller_uid.rs` | 159 | Caller UID resolution for auth |
| `auth.rs` | 149 | Authorization checks |
| `policykit.rs` | 110 | PolicyKit integration |

Known caveat (see `docs/BLOCKERS.md`): secured captive SSIDs (WPA2/EAP) not
yet supported — open SSIDs only.

## Distribution artifacts
`desktop/gatepath-netns-helper/packaging/` — systemd-sysext package (P2.1, #59),
D-Bus/PolicyKit config, tmpfiles.d.
