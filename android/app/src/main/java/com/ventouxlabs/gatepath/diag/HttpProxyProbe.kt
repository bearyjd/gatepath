package com.ventouxlabs.gatepath.diag

/**
 * Reads [ProbeContext.httpProxyDescription] and returns
 * [DiagnosticReport.HttpProxyBlocking] when the network has a per-network
 * HTTP proxy (static or PAC) configured. Most captive gateways don't route
 * their redirect through the proxy, so sign-in silently dies.
 *
 * No network call — completes synchronously inside the per-probe budget.
 */
class HttpProxyProbe : DiagnosticProbe {
    override val name = "http_proxy"

    override suspend fun run(ctx: ProbeContext): DiagnosticReport =
        when (val proxy = ctx.httpProxyDescription) {
            null -> DiagnosticReport.Healthy
            else -> DiagnosticReport.HttpProxyBlocking(description = proxy)
        }
}
