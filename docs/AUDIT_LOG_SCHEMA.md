# Gatepath Audit Log Schema

Both platforms write JSONL (one JSON object per line) to an append-only file:

- **Android:** `<filesDir>/audit.jsonl` (app-private, not world-readable)
- **Desktop:** `${XDG_DATA_HOME:-$HOME/.local/share}/gatepath/audit.jsonl`

The **machine-readable contract** lives in [`audit_log_schema.json`](audit_log_schema.json).
Both platforms' test suites load that file and assert their writer's output conforms,
so this Markdown is for humans; the JSON is the source of truth.

Every entry **must** validate against the schema below. `schema_version: 1` is the only
currently defined version. Increment it for any breaking change.

```json
{
  "schema_version": 1,
  "timestamp_utc": "2026-05-05T12:34:56.000Z",
  "platform": "android",
  "ssid": "Airport-WiFi",
  "gateway_ip": "192.168.0.1",
  "portal_domain": "wifi.example-airport.com",
  "vpn_interfaces_detected": ["tailscale0 (full_tunnel)"],
  "vpn_warning_shown": true,
  "session_opened_utc": "2026-05-05T12:34:00.000Z",
  "session_closed_utc": "2026-05-05T12:36:42.000Z",
  "close_reason": "portal_completed",
  "duration_seconds": 162,
  "blocked_navigation_attempts": 2,
  "blocked_resource_requests": 11
}
```

## Field reference

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `int` | Always `1` for this revision. |
| `timestamp_utc` | `string` (ISO 8601, UTC, `Z` suffix) | When the entry was written. |
| `platform` | `"android" \| "desktop"` | Which app produced the entry. |
| `ssid` | `string \| null` | WiFi SSID if known and permitted. |
| `gateway_ip` | `string \| null` | IPv4 of the gateway, if known. |
| `portal_domain` | `string` | Host of the captive portal URL. |
| `vpn_interfaces_detected` | `string[]` | Each entry: `"<iface> (<mode>)"`, mode is `full_tunnel`, `split_tunnel`, or `unknown`. |
| `vpn_warning_shown` | `bool` | `true` if the user was warned before the session opened. |
| `session_opened_utc` | `string` (ISO 8601) | When the portal window was opened. |
| `session_closed_utc` | `string \| null` (ISO 8601) | `null` only if the entry is for a session that never closed (should not happen for normal exit). |
| `close_reason` | `"portal_completed" \| "user_dismissed" \| "timeout" \| "error" \| "aborted_pre_active"` | Non-null required. See enum below. |
| `duration_seconds` | `int` | Whole seconds between open and close. `0` is valid for `aborted_pre_active`. |
| `blocked_navigation_attempts` | `int` | Off-domain navigations the WebView refused. **Same meaning on both platforms.** |
| `blocked_resource_requests` | `int` | **Platform-specific meaning** — see below. |

## `close_reason` enum

| Value | Meaning |
|---|---|
| `portal_completed` | Probe returned 204 / NM reported FULL connectivity — sign-in succeeded. |
| `user_dismissed` | User closed the portal window before completion. |
| `timeout` | 10-minute session limit reached. |
| `error` | Unrecoverable error during an active session. |
| `aborted_pre_active` | Session was terminated before the portal window opened (e.g. network lost during `Detected` phase). `duration_seconds` will be `0` and `session_closed_utc` will equal `session_opened_utc` (or be `null` if the session never opened). |

## Platform-specific field semantics

`blocked_resource_requests` does NOT mean the same thing on both platforms:

- **Android:** count of resource sub-requests the WebView **refused to load** via
  `WebViewClient.shouldInterceptRequest`. Each counted request was kernel-level cancelled.
- **Desktop:** count of resource sub-requests that **matched** a tracker domain.
  WebKitGTK's `resource-load-started` signal is informational only — the request is
  observed and logged but **not cancelled**. See `SECURITY_MODEL.md` for why this
  isn't enforced on desktop.

If you build cross-platform analytics over these logs, normalise this field by platform
or treat the desktop value as a different metric.

## Reading

Both platforms expose a `read_all()` helper that returns entries in chronological order
(file order; entries are append-only and never edited). The audit viewer in the UI MUST
treat the file as read-only.

## Privacy

The log lives in app-private storage. No identifying user data (browser cookies, form
inputs, exact URL paths beyond the domain) is recorded.
