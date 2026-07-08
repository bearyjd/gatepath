<!-- Generated: 2026-07-05 | Files scanned: audit_log_schema.json + writers | Token estimate: ~350 -->

# Data Codemap — Audit Log Schema

No database. The only cross-platform "data model" is the shared audit-log
record, defined once and enforced by both platforms' test suites.

**Source of truth:** `docs/audit_log_schema.json` (machine-readable, `schema_version: 1`)
Human docs: `docs/AUDIT_LOG_SCHEMA.md`

## Record shape
```
schema_version: int
timestamp_utc, session_opened_utc, session_closed_utc?: string (ISO 8601)
platform: "android" | "desktop"
ssid?, gateway_ip?: string | null            (redactable — see collect-diagnostics.sh)
portal_domain: string                        (redactable; may be empty iff close_reason == aborted_pre_active)
vpn_interfaces_detected: array<string>
vpn_warning_shown: bool
close_reason: "portal_completed" | "user_dismissed" | "timeout" | "error" | "aborted_pre_active"
duration_seconds: int
blocked_navigation_attempts: int   (observed + counted, NOT blocked — legacy field name)
blocked_resource_requests: int    (observed + counted, NOT blocked — legacy field name)
```

## Writers / readers
| Side | Writer | Notes |
|------|--------|-------|
| Desktop | `desktop/gatepath/audit_log.py` | Redacts ssid/gateway_ip/portal_domain per `docs/TROUBLESHOOTING.md` |
| Desktop (root) | `desktop/gatepath-netns-helper/src/audit_log.rs` | Separate privileged-side JSONL, `/var/lib/gatepath/helper-audit.jsonl` in production (via systemd `StateDirectory`) |
| Android | `android/.../audit/AuditLog.kt`, `AuditEntry.kt` | |

Both platforms' test suites load `audit_log_schema.json` directly and assert
their writer's output conforms — this is the only enforced cross-language
contract outside the D-Bus `RefusalReason` enum (see backend.md).

## Other structured config (not "data" per se, but schema-like)
- `distribution/fdroid/com.ventouxlabs.gatepath.yml` — F-Droid metadata
- `distribution/flathub/com.ventouxlabs.Gatepath.yml` — Flathub manifest
- D-Bus/PolicyKit policy files under `desktop/gatepath-netns-helper/packaging/`
