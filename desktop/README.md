# Gatepath Desktop

Captive portal handler for Linux desktop, distributed as a Flatpak.

**Flatpak ID:** `com.ventouxlabs.Gatepath`

## What it does

Gatepath monitors NetworkManager for captive portal detection signals and opens
an isolated WebKitGTK window for safe portal sign-in. It:

- Restricts WebView navigation to the portal's origin domain only.
- Blocks third-party tracker/analytics resource requests.
- Wipes all session data (cookies, cache, localStorage) on close.
- Auto-closes after 10 minutes.
- Detects active VPN interfaces and warns before opening the portal.
- Writes an append-only audit log to `~/.local/share/gatepath/audit.jsonl`.

## Desktop limitations

See [`docs/SECURITY_MODEL.md`](../docs/SECURITY_MODEL.md) for the full security
model. Key desktop-specific limitations:

- **Cannot bind WebKit traffic to a specific network interface** — the Flatpak
  sandbox does not grant `CAP_NET_RAW`. If a full-tunnel VPN is active, the
  portal page may not load.
- **Recommendation:** pause your VPN before connecting to a captive portal on
  desktop. Gatepath will remind you.

## Requirements

- GNOME Platform 46 (via Flatpak)
- GTK 4 + libadwaita
- WebKit2GTK 6.0 (preferred) or 4.1

## Running without Flatpak (development)

```bash
# Install GUI extras (requires PyGObject system package)
pip install -e ".[dev]"

# Run without GTK (shows --help only)
python -m gatepath --help

# Run with GTK installed
python -m gatepath
```

## Running tests

```bash
# From the repo root:
python3 -m pytest desktop/ mockportal/ -v
```

## Project layout

```
gatepath/
├── __main__.py        Entry point (argparse before GTK)
├── app.py             Adw.Application (GTK import guarded)
├── window.py          AdwApplicationWindow (GTK import guarded)
├── portal_monitor.py  NM / polling monitor (stdlib top-level)
├── portal_probe.py    urllib probe (pure stdlib)
├── portal_session.py  State machine (pure stdlib)
├── portal_webview.py  WebKit view (GTK import guarded)
├── blocked_domains.py Tracker domain list (pure stdlib)
├── vpn_detector.py    VPN detection (pure stdlib)
└── audit_log.py       JSONL audit writer (pure stdlib)
```
