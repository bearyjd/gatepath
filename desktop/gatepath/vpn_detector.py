"""VPN interface detector — pure stdlib (socket + urllib).

Classifies network interfaces by name to detect active VPN connections.
For Tailscale, queries the local API to determine if an exit node is active
(full-tunnel mode).

Injectable `_open` parameter allows tests to monkeypatch urllib.request.urlopen.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Interface name prefixes that indicate VPN usage.
# Source of truth: docs/SECURITY_MODEL.md "VPN-interface prefixes" section.
# Common prefixes (also detected by Android):
#   tun, tap, wg, ipsec, ppp, tailscale, torguard
# Desktop-only additions for Linux vendor-named interfaces:
#   proton, nordvpn
_VPN_PREFIXES = (
    "tun", "tap", "wg", "ipsec", "ppp", "tailscale", "torguard",
    "proton", "nordvpn",
)
_TAILSCALE_NAMES = frozenset({"tailscale0", "ts0"})

TAILSCALE_STATUS_URL = "http://localhost:41112/localapi/v0/status"


@dataclasses.dataclass(frozen=True)
class VpnInterface:
    """Description of a detected VPN interface."""

    name: str
    mode: str  # "full_tunnel" | "split_tunnel" | "unknown"

    def label(self) -> str:
        """Return the audit-log format: '<name> (<mode>)'."""
        return f"{self.name} ({self.mode})"


def _is_tailscale_full_tunnel(
    _open: Callable = urllib.request.urlopen,
) -> bool:
    """Return True if Tailscale has an active exit node (full-tunnel mode)."""
    try:
        req = urllib.request.Request(TAILSCALE_STATUS_URL)
        with _open(req, timeout=2) as resp:
            data = json.loads(resp.read())
        exit_node_id = data.get("ExitNodeID", "") or ""
        return bool(exit_node_id)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        logger.debug("Tailscale API unreachable or error: %s", exc)
        return False


def _classify_interface(
    name: str,
    _open: Callable = urllib.request.urlopen,
) -> Optional[VpnInterface]:
    """Return a VpnInterface for *name* if it looks like a VPN, else None."""
    lower = name.lower()

    if name in _TAILSCALE_NAMES:
        is_full = _is_tailscale_full_tunnel(_open)
        mode = "full_tunnel" if is_full else "split_tunnel"
        return VpnInterface(name=name, mode=mode)

    if any(lower.startswith(p) for p in _VPN_PREFIXES):
        return VpnInterface(name=name, mode="unknown")

    return None


def detect_vpn_interfaces(
    _open: Callable = urllib.request.urlopen,
) -> list[str]:
    """Return list of VPN interface labels in audit-log format.

    Uses socket.if_nameindex() to enumerate interfaces; injectable
    _open callable for Tailscale API queries.
    """
    try:
        interfaces = socket.if_nameindex()
    except OSError as exc:
        logger.warning("Could not enumerate network interfaces: %s", exc)
        return []

    results: list[str] = []
    for _idx, name in interfaces:
        vpn = _classify_interface(name, _open)
        if vpn is not None:
            logger.info("VPN interface detected: %s", vpn.label())
            results.append(vpn.label())

    return results
