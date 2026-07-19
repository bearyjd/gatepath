"""Reports a configured HTTP proxy as a likely cause of stuck sign-in.

Reads `ProbeContext.http_proxy_description` and returns
`HttpProxyBlocking` when the network has a per-network HTTP proxy (static
or PAC) configured. Most captive gateways don't route their redirect
through the proxy, so sign-in silently dies.

Mirror of Android `HttpProxyProbe.kt`.

Context-only — no network access.
"""

from __future__ import annotations

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import DiagnosticReport, Healthy, HttpProxyBlocking


class HttpProxyProbe:
    name = "http_proxy"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        if ctx.http_proxy_description is None:
            return Healthy()
        return HttpProxyBlocking(description=ctx.http_proxy_description)
