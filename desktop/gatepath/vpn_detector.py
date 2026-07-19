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

# Cap how much of the localapi status body is read into memory before parsing.
# Set well above any realistic /v0/status size (which scales with tailnet peer
# count) so a legitimate large response is never truncated; this only bounds a
# runaway or hostile local endpoint. Over-limit bodies fail safe to
# split-tunnel, consistent with the rest of this best-effort detector.
_MAX_STATUS_BYTES = 8 * 1024 * 1024


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
    _max_bytes: int = _MAX_STATUS_BYTES,
) -> bool:
    """Return True if Tailscale has an active exit node (full-tunnel mode).

    The localapi /v0/status response reports the selected exit node under a
    nested ExitNodeStatus object with a non-empty ``ID``; that object is
    omitted entirely when no exit node is set. There is no top-level
    ``ExitNodeID`` field on the status response.

    At most ``_max_bytes`` of the response body are read; a larger body fails
    safe to False rather than being pulled into memory unbounded.
    """
    try:
        req = urllib.request.Request(TAILSCALE_STATUS_URL)
        with _open(req, timeout=2) as resp:
            raw = resp.read(_max_bytes + 1)
        if len(raw) > _max_bytes:
            logger.debug(
                "Tailscale status body exceeded %d bytes; ignoring", _max_bytes
            )
            return False
        data = json.loads(raw)
        exit_node_status = data.get("ExitNodeStatus")
        if not isinstance(exit_node_status, dict):
            return False
        node_id = exit_node_status.get("ID")
        # A StableNodeID is always a string; require a non-empty one so a
        # non-string value can't be treated as a live exit node (parity with
        # Android's primitive-string check).
        return isinstance(node_id, str) and node_id != ""
    except (
        urllib.error.URLError,
        OSError,
        json.JSONDecodeError,
        AttributeError,
    ) as exc:
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


def detect_vpn_details(
    _open: Callable = urllib.request.urlopen,
) -> list[VpnInterface]:
    """Return structured VpnInterface entries for detected VPN interfaces.

    Uses socket.if_nameindex() to enumerate interfaces; injectable
    _open callable for Tailscale API queries.
    """
    try:
        interfaces = socket.if_nameindex()
    except OSError as exc:
        logger.warning("Could not enumerate network interfaces: %s", exc)
        return []

    results: list[VpnInterface] = []
    for _idx, name in interfaces:
        vpn = _classify_interface(name, _open)
        if vpn is not None:
            logger.info("VPN interface detected: %s", vpn.label())
            results.append(vpn)

    return results


def detect_vpn_interfaces(
    _open: Callable = urllib.request.urlopen,
) -> list[str]:
    """Return VPN interface labels in audit-log format.

    Thin wrapper over [detect_vpn_details] — the label format is the
    audit-log contract, so it stays defined in exactly one place.
    """
    return [vpn.label() for vpn in detect_vpn_details(_open)]
