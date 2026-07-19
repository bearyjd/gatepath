"""Reports a VPN that is likely blocking captive sign-in.

Any VPN interface is reported: even split-tunnel setups routinely install
DNS rules that break captive resolution, so the finding is worth surfacing.
`is_full_tunnel` tells the UI how certain the "pause your VPN" advice is.

Mirror of Android `VpnProbe.kt`, with one structural divergence: Android
falls back to the name "tailscale" when an exit node is active but interface
enumeration matched nothing. Desktop's [ProbeContext] carries full-tunnel
status only per-interface (inside `VpnDetail`), so an empty `vpn_interfaces`
tuple cannot encode that case and the fallback has no data to key off.

Context-only — no network access.
"""

from __future__ import annotations

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import DiagnosticReport, Healthy, VpnBlocking


class VpnProbe:
    name = "vpn"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        if not ctx.vpn_interfaces:
            return Healthy()
        first = ctx.vpn_interfaces[0]
        return VpnBlocking(interface_name=first.name, is_full_tunnel=first.is_full_tunnel)
