# Troubleshooting Gatepath

Operator- and user-facing guide for diagnosing the **desktop helper**
(`gatepath-netns-helper`) and the **Android app**. See also:
[`SECURITY_MODEL.md`](SECURITY_MODEL.md) (what Gatepath does/doesn't protect),
[`DESKTOP_NETNS_DEPLOYMENT.md`](DESKTOP_NETNS_DEPLOYMENT.md) (install),
[`AUDIT_LOG_SCHEMA.md`](AUDIT_LOG_SCHEMA.md) (log fields),
[`BLOCKERS.md`](BLOCKERS.md) (known limitations).

Two intentional limitations up front, because they explain most "it won't work"
reports: Gatepath supports **open captive networks only** (WPA/WPA2/WPA3/EAP are
refused), and the desktop helper needs **root / `CAP_NET_ADMIN`** (there is no
unprivileged path — see DESKTOP_NETNS_DEPLOYMENT.md §2).

---

## Collect a support bundle (desktop)

```bash
sudo desktop/gatepath-netns-helper/packaging/collect-diagnostics.sh
#   → gatepath-diagnostics-<host>-<UTC>.tar.gz
sudo .../collect-diagnostics.sh --redact     # strip SSIDs + gateway IPs + portal domains
```

It gathers `systemctl status` + journal for the unit, `systemd-sysext status`,
netns + NetworkManager state, tool versions, and the helper + user audit logs.
**Run it with `sudo`** or the root-owned helper audit log
(`/var/lib/gatepath/helper-audit.jsonl`, `0640`) is skipped. **Review the bundle
before sharing** — see _Privacy_ below.

---

## Desktop: preconditions (all must hold)

These mirror what the integration harness (`tests/e2e-hwsim/preflight.sh`)
checks. If a `SetupCaptive` fails, walk this list first.

| Requirement | Check | If missing |
|---|---|---|
| Helper installed + activatable | `systemctl status gatepath-netns-helper` | install the sysext (DEPLOYMENT §6) + `systemctl daemon-reload` |
| On an immutable host, sysext merged | `systemd-sysext status` shows `gatepath-netns-helper` | `systemd-sysext merge` |
| Runs as root w/ `CAP_NET_ADMIN` | unit has `User=root` | don't override the unit's user |
| **NetworkManager active** | `systemctl is-active NetworkManager` | start/enable NetworkManager |
| Tools on PATH | `iw`, `wpa_supplicant`, `dnsmasq`, `nmcli`, `ip` present | install them (the sysext ships only the helper, not these) |
| PolicyKit action registered | an auth prompt appears on first use | reinstall the `.policy`; on read-only `/usr` it must be merged, not bind-mounted away |
| Not inside a container | `ls /run/.containerenv /.dockerenv` absent | run on the host; netns + PHY move need the real kernel |
| **Open** captive network | the network has no Wi-Fi password | secured networks are unsupported (returns `unsupported_security`) |

---

## Desktop: refusal reasons

Every helper decision is logged to `/var/lib/gatepath/helper-audit.jsonl` and, on
refusal, returned as a typed D-Bus error
(`com.ventouxlabs.Gatepath.NetNsHelper.Error.<PascalCase>`). Find the audit
`decision.reason` (snake_case) or the error suffix and look it up:

| `decision.reason` | What it means | What to do |
|---|---|---|
| `invalid_interface` | The name isn't a usable Wi-Fi interface (`wlan*`/`wlp*`/`wlx*`), or doesn't exist. VPN/ethernet/bridge names are refused by design. | Pass your real Wi-Fi device (`nmcli device status`). |
| `not_captive` | NetworkManager doesn't flag this network as captive. | Confirm you're on the captive Wi-Fi; check `nmcli -f GENERAL.STATE,IP4.CONNECTIVITY device show <iface>` reads `portal`. |
| `pending` | NetworkManager is still evaluating connectivity. | Wait a few seconds and retry. |
| `unauthorised` | PolicyKit denied — you cancelled the prompt, or the action isn't registered. | Authenticate at the prompt; verify the `.policy` is installed. |
| `backend_unavailable` | The NetworkManager D-Bus service is unreachable. | `systemctl status NetworkManager`; restart it. |
| `kernel_error` | A kernel op failed during the PHY move / netns migration. | Check `journalctl -u gatepath-netns-helper`; ensure `iw`/`ip` present and the card supports `iw phy set netns`. |
| `already_active` | A previous session is still active. | Tear down first; clear a stale netns with `sudo ip netns del gatepath`, or restart the helper. |
| `throttled` | You hit the per-sender rate limit (prompt-fatigue DoS guard). | Back off and retry after a moment; don't loop. |
| `invalid_portal_url` | The portal URL failed validation (non-`http(s)`, control bytes, unparseable). | Usually internal; capture the URL the app passed and file a bug. |
| `invalid_display_env` | A graphical-session value (`WAYLAND_DISPLAY`/`DISPLAY`/`XAUTHORITY`) failed validation. | Launch from a real graphical session; check those env vars. |
| `no_active_session` | `LaunchPortal` was called without a prior `SetupCaptive`. | Internal ordering; restart the flow. |
| `sender_mismatch` | A different bus client tried to use another client's session. | Single-session enforcement; only the owner can launch. |
| `spawn_failed` | The portal-WebView transient `systemd-run` unit failed to launch. | Check the journal; ensure systemd ≥ 252 and WebKitGTK are present. |
| `unsupported_security` | The captive network is **secured** (WPA/WPA2/WPA3/EAP). | Not a bug — open networks only today. See BLOCKERS.md. |

---

## Diagnosis findings

The diagnosis panel ships on **both platforms** — Android (`MainScreen`, run
automatically) and desktop (the **Run diagnostics** button in the monitoring
window, manual-run today). It runs probes and surfaces the highest-severity
finding plus a recommended action. The two engines share one **12-cause**
vocabulary (Android `DiagnosticReport` ↔ desktop `Cause`, kept in sync by a
cause-parity drift guard); two causes are **Android-only** because they name
Android platform concepts with no desktop equivalent — flagged in the table.

| Finding | What it means | What to do |
|---|---|---|
| `vpn_blocking` | A full-tunnel VPN is blocking captive sign-in. | Disable the VPN, sign in, then re-enable it. |
| `dns_hijack` | The gateway is hijacking DNS beyond the connectivity check. | Informational; expected on some captive gateways. |
| `private_dns_blocking` | Strict **DNS-over-TLS / Private DNS** is active and the captive net blocks it. | **Android:** Settings → Network → Private DNS → **Off/Automatic** during sign-in. **Desktop:** disable strict systemd-resolved DoT (`DNSOverTLS=yes`) for this network, sign in, re-enable. |
| `http_proxy_blocking` | An HTTP proxy is configured on this network. | Remove the proxy for this Wi-Fi, sign in, restore it. |
| `sandboxed_webview` *(Android-only)* | The WebView ran in a sandboxed subprocess without network binding. | Device/WebView issue; update Android System WebView. |
| `https_only_captive` | The captive portal blocks HTTPS outright. | Network-side; try the http:// connectivity-check URL. |
| `cellular_fallback` *(Android-only)* | Cellular is masking the captive Wi-Fi state. | Toggle mobile data off briefly so the portal is forced over Wi-Fi. |
| `no_dns_servers` | DHCP handed this network zero DNS servers — a half-broken connect. | Reconnect to the Wi-Fi so DHCP completes and hands out resolvers. |
| `portal_redirect_loop` | The sign-in redirect chain revisits a URL it already issued. | Gateway-side loop (often a stale auth cookie); clear cookies/reconnect. |
| `clock_skew` | The device clock disagrees with the gateway beyond tolerance, breaking TLS. | Fix the date/time (enable automatic time), then retry sign-in. |
| `inconclusive` | No probe could conclude. | See the probe errors in the report; collect logs. |
| `healthy` | No problem detected. | If sign-in still fails, capture the audit log and file a bug. |

The two Android-only causes (`sandboxed_webview`, `cellular_fallback`) name
Android platform mechanisms — the Android WebView process model and a cellular
radio to fall back onto — that the desktop app has no analogue for, so the
desktop `Cause` set is these 12 minus those 2 (= 10). (`private_dns_blocking`
used to be Android-only; desktop now detects strict systemd-resolved
DNS-over-TLS, so it is shared.)

---

## Reading the audit logs

| | Path | Owner |
|---|---|---|
| Desktop helper | `/var/lib/gatepath/helper-audit.jsonl` | `root:gatepath` `0640` (read with sudo) |
| Desktop GUI | `${XDG_DATA_HOME:-~/.local/share}/gatepath/audit.jsonl` | your user |
| Android | `<app filesDir>/audit.jsonl` | app-private |

Both are JSONL (one JSON object per line). Useful `jq` over the helper log:

```bash
# Every refusal and why:
sudo jq -c 'select(.decision.kind=="refused") | {timestamp_utc, action, reason: .decision.reason}' \
  /var/lib/gatepath/helper-audit.jsonl

# Count refusal reasons:
sudo jq -r 'select(.decision.kind=="refused") | .decision.reason' \
  /var/lib/gatepath/helper-audit.jsonl | sort | uniq -c | sort -rn
```

A helper entry looks like:

```json
{"timestamp_utc":"2026-05-09T10:30:00Z","action":"setup_captive","sender":":1.42","interface":"wlan0","decision":{"kind":"success"}}
{"timestamp_utc":"2026-05-09T10:31:00Z","action":"setup_captive","sender":":1.42","interface":"wlan0","decision":{"kind":"refused","reason":"unsupported_security"}}
```

`action` ∈ `setup_captive` · `teardown_captive` · `auto_teardown` · `launch_portal`.

---

## Common scenarios

- **The portal never opens.** Check the last audit `decision` (refusal table
  above). If it's `success` but no window appears, suspect the WebView launch
  (`spawn_failed` / journal) or the display env (`invalid_display_env`).
- **It asks for an admin password every time.** Expected on the first call per
  session (`auth_admin_keep`). Re-prompting *every* call means the session
  auth isn't being kept — check PolicyKit and that you're in an active session.
- **"This network isn't supported."** `unsupported_security` — the Wi-Fi is
  password-protected; Gatepath handles open captive networks only today.
- **Nothing happens / `backend_unavailable`.** NetworkManager isn't running or
  reachable. `systemctl status NetworkManager`.
- **Worked once, now `already_active`.** A prior session didn't tear down.
  `sudo ip netns del gatepath` and retry, or restart the helper.

When filing a bug, attach the `--redact`'d support bundle.

---

## Privacy

The desktop helper audit log records the **D-Bus sender**, the **Wi-Fi interface
name**, and **timestamps** — not the SSID or your traffic. The GUI/Android logs
additionally record the **SSID** and **gateway IP** (never page contents or
credentials; see AUDIT_LOG_SCHEMA.md). The support bundle also includes
NetworkManager and journald output, which can contain SSIDs. Pass `--redact` to
strip SSIDs, gateway IPs, and portal domains from the copied audit logs (it does
**not** scrub the journald/nmcli captures), and review the bundle before sharing.
