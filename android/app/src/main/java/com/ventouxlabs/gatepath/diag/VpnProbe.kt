package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.vpnInterfaces] / [ProbeContext.isTailscaleFullTunnel]
 * and returns [DiagnosticReport.VpnBlocking] when a VPN is up while the
 * captive portal is unresolved.
 *
 * Any VPN interface is reported — even split-tunnel setups routinely install
 * DNS rules that break captive resolution, so the finding is worth surfacing;
 * [DiagnosticReport.VpnBlocking.isFullTunnel] tells the UI how certain the
 * "pause your VPN" advice is. A Tailscale exit node without a matched
 * interface name (interface enumeration can race teardown) still reports,
 * with a fallback name.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class VpnProbe : DiagnosticProbe {
    override val name = "vpn"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport {
        val interfaceName = ctx.vpnInterfaces.firstOrNull()
            ?: if (ctx.isTailscaleFullTunnel) "tailscale" else return DiagnosticReport.Healthy
        return DiagnosticReport.VpnBlocking(
            interfaceName = interfaceName,
            isFullTunnel = ctx.isTailscaleFullTunnel,
        )
    }
}
