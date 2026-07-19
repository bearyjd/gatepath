"""Detects a device clock badly out of sync with the captive gateway.

Compares the device clock (`ProbeContext.now_epoch_seconds`) against the
gateway's HTTP `Date` header (one `ProbeContext.http_fetch` of the probe
URL). A clock off by more than `SKEW_TOLERANCE_SECONDS` breaks TLS
certificate validation, making HTTPS portal pages fail in ways users read as
"the Wi-Fi is broken."

The gateway's own clock could be the wrong one — the report says the two
disagree, and the recommended action (enable automatic date & time) is safe
either way. Missing header or failed fetch is Healthy: absence of evidence,
and other probes surface unreachability better.

Mirror of Android `ClockSkewProbe.kt`.
"""

from __future__ import annotations

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import ClockSkew, DiagnosticReport, Healthy

# Tolerance before a clock difference is a finding; captive gateways are
# rarely this wrong.
SKEW_TOLERANCE_SECONDS = 300


class ClockSkewProbe:
    name = "clock_skew"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        result = ctx.http_fetch(ctx.probe_url, None)
        gateway_seconds = result.date_epoch_seconds
        if gateway_seconds is None:
            return Healthy()
        skew_seconds = int(abs(ctx.now_epoch_seconds() - gateway_seconds))
        if skew_seconds > SKEW_TOLERANCE_SECONDS:
            return ClockSkew(skew_seconds=skew_seconds)
        return Healthy()
