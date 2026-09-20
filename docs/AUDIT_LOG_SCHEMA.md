# Gatepath Audit Log Schema

Both platforms write JSONL (one JSON object per line) to an append-only file:

- **Android:** `<filesDir>/audit.jsonl` (app-private, not world-readable)
- **Desktop:** `${XDG_DATA_HOME:-$HOME/.local/share}/gatepath/audit.jsonl`

The **machine-readable contract** lives in [`audit_log_schema.json`](audit_log_schema.json).
Both platforms' test suites load that file and assert their writer's output conforms,
so this Markdown is for humans; the JSON is the source of truth.

Every entry **must** validate against the schema below. Currently `schema_version: 2`.
Increment it for any breaking change.

**Adding** a field is not a breaking change and does not bump the version: new
fields go in the JSON contract's `optional_fields` list. Writers on both
platforms must emit every optional field; readers must tolerate its absence,
because lines written before the field existed are still valid `v1` (with
`v1_key_renames` applied). Removing a field, or changing an existing one,
*is* breaking and does require a bump.

```json
{
  "schema_version": 2,
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
  "observed_navigation_attempts": 2,
  "observed_resource_requests": 11,
  "confinement": "confined",
  "tls_cert_errors_bypassed": 0
}
```

## Field reference

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `int` | Always `2` for this revision. |
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
| `observed_navigation_attempts` | `int` | Off-domain navigations the WebView observed. Counted and allowed to load. Same meaning on both platforms. See SECURITY_MODEL.md. |
| `observed_resource_requests` | `int` | Tracker-domain subresource requests the WebView observed. Counted and allowed to load. Same meaning on both platforms. See SECURITY_MODEL.md. |
| `confinement` | `"confined" \| "unconfined"` | Network confinement state. **Android:** `"confined"` only for a session opened from a classified `Confined` state, which requires the Wi-Fi-bound probe to have reached the gateway — netd permits that only when no VPN covers Gatepath (or Gatepath is excluded from it). The debug-force path opens a session without classifying and writes `"unconfined"`. **Desktop:** `"confined"` when the netns helper launched the portal WebView inside the gatepath namespace; `"unconfined"` for the in-process (Flatpak-only) path where WebKitGTK traffic follows the system default route. See SECURITY_MODEL.md. |
| `tls_cert_errors_bypassed` | `int` | *Optional field (added after v1 shipped).* Certificate errors the WebView was told to proceed past on the portal host. **Android:** count of `onReceivedSslError` → `handler.proceed()` calls, which only ever happen for the portal host and its subdomains; cert errors on any other host are cancelled and not counted. Non-zero means the session rendered a page whose certificate did not validate. **Desktop:** count of TLS errors proceeded past (gated by `ssl_error_policy` — same host-scoped rule as Android). The count travels through the portal observation channel (`portal_observations`); 0 when that file is missing or unreadable. Non-zero means the session rendered a page whose certificate did not validate. |

## `close_reason` enum

| Value | Meaning |
|---|---|
| `portal_completed` | Probe returned 204 / NM reported FULL connectivity — sign-in succeeded. |
| `user_dismissed` | User closed the portal window before completion. |
| `timeout` | 10-minute session limit reached. |
| `error` | Unrecoverable error during an active session. |
| `aborted_pre_active` | Session was terminated before the portal window opened — either by an involuntary event (network lost during `Detected` phase) or by the user dismissing the portal banner before opening the window. `duration_seconds` will be `0` and `session_closed_utc` will equal `session_opened_utc` (synthetic timestamps, both stamped at close time). `portal_domain` MAY be empty when the session never observed a portal URL (e.g., dismissal during `Monitoring`). For all other `close_reason` values, `portal_domain` is required and non-empty. |

## Version history

### Schema v1 (2026-05)

Initial version. Renamed in v2 to "observed" (was "blocked").

### Schema v2 (current)

- Renamed `blocked_navigation_attempts` → `observed_navigation_attempts` and
  `blocked_resource_requests` → `observed_resource_requests` to clarify that
  these are counts of observed requests, not cancelled requests. The WebView
  allows these to load and counts them in the audit log. Readers must apply
  `v1_key_renames` (see `audit_log_schema.json`) when decoding v1 lines so old
  counts stay intact.
- Added `confinement` field (required): `"confined"` or `"unconfined"`, tracking
  whether the portal WebView ran inside a network namespace (confined) or used
  the system default route (unconfined). Platform-specific semantics in the JSON.
- Updated `tls_cert_errors_bypassed` documentation to reflect current behavior:
  desktop now counts real TLS bypasses (from the portal observation channel),
  not always 0.

## Reading

Both platforms expose a `read_all()` helper that returns entries in chronological order
(file order; entries are append-only and never edited). The audit viewer in the UI MUST
treat the file as read-only.

## Privacy

The log lives in app-private storage. No identifying user data (browser cookies, form
inputs, exact URL paths beyond the domain) is recorded.
