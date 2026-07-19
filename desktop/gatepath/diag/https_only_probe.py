"""Detects captive setups that pass cleartext HTTP but kill HTTPS.

Re-runs the HTTP verdict via `ProbeContext.active_probe`, and only when HTTP
says *validated* does it try the https-scheme variant of
`ProbeContext.probe_url` via `ProbeContext.http_fetch`. HTTPS failing while
HTTP works (TLS interception, RST-on-443) is the `HttpsOnlyCaptive`
signature.

While the network is still captive (HTTP -> portal) or broken (HTTP ->
error), HTTPS failing tells us nothing new — those cases are Healthy here
and owned by other probes.

Mirror of Android `HttpsOnlyProbe.kt`. Desktop's `ProbeResult.status` is the
string "validated" | "portal" | "error", not a sealed type.
"""

from __future__ import annotations

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import DiagnosticReport, Healthy, HttpsOnlyCaptive


class HttpsOnlyProbe:
    name = "https_only"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        http_result = ctx.active_probe()
        if http_result.status != "validated":
            return Healthy()
        https_url = ctx.probe_url.replace("http://", "https://", 1)
        https_result = ctx.http_fetch(https_url, None)
        if https_result.error is not None:
            return HttpsOnlyCaptive(https_error_message=https_result.error)
        return Healthy()
