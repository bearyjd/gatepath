# Gatepath Desktop

Captive portal handler for Linux desktop, distributed as a native RPM or Flatpak.

**Flatpak ID:** `com.ventouxlabs.Gatepath`

## What it does

Gatepath monitors NetworkManager for captive portal detection signals and opens
an ephemeral WebKitGTK window for portal sign-in. When the optional host netns
helper is available, it runs the portal WebView in a dedicated network namespace.
Without that helper, the WebView uses the normal host route. It:

- Observes and counts off-origin navigations and third-party tracker/analytics
  resource requests, but allows them so captive portals remain compatible.
- Wipes all session data (cookies, cache, localStorage) on close.
- Auto-closes after 10 minutes.
- Detects active VPN interfaces and warns before opening the portal.
- Writes an append-only audit log to `~/.local/share/gatepath/audit.jsonl`.

## Desktop limitations

See [`docs/SECURITY_MODEL.md`](../docs/SECURITY_MODEL.md) for the full security
model. Key desktop-specific limitations:

- **Host helper available:** the privileged `gatepath-netns-helper` moves the
  captive Wi-Fi PHY into a dedicated network namespace and runs the portal
  WebView there. This path is validated with `mac80211_hwsim` for open captive
  networks; physical Wi-Fi-card confirmation remains pending.
- **Helper unavailable (including Flatpak-only installs):** the in-process
  WebView is not bound to a specific network interface. If a full-tunnel VPN
  is active, the portal page may not load. Gatepath warns before opening it;
  pausing the VPN is the documented mitigation.

## Requirements

- GNOME Platform 46 (via Flatpak)
- GTK 4 + libadwaita
- WebKit2GTK 6.0 (preferred) or 4.1

## Native Fedora installation

The `gatepath-netns-helper` RPM includes the desktop app, portal runner, and
privileged helper in one installation. Its package name is retained for upgrades
from older helper-only releases. GTK4, libadwaita, WebKitGTK 6.0, Python D-Bus
bindings, and network setup tools are required dependencies installed by DNF.

```bash
# From the repo root (install the build dependencies listed in the script first):
desktop/gatepath-netns-helper/packaging/build-rpm.sh
sudo dnf install ~/rpmbuild/RPMS/*/gatepath-netns-helper-*.rpm
gatepath
```

You can also launch Gatepath from the desktop application menu. A separate
Python installation or Flatpak is unnecessary. Building uses committed source;
commit local changes before building an RPM. Atomic Fedora systems can layer
the RPM with `rpm-ostree install` and reboot; see
[`DESKTOP_NETNS_DEPLOYMENT.md`](../docs/DESKTOP_NETNS_DEPLOYMENT.md) for deployment
options and the physical Wi-Fi validation limitation.

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
