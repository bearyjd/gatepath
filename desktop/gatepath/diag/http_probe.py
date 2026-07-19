"""Re-runs the active HTTP connectivity probe and surfaces its raw error.

Mirror of Android `HttpProbe.kt`. Re-invokes `ProbeContext.active_probe`
(the same connectivity probe that classified this network as captive in the
first place) and translates the outcome:

  - "validated" or "portal" -> Healthy (the path is usable; whatever tripped
    the caller into running diagnostics has cleared, or a portal is present
    and answering as expected)
  - "error" -> Inconclusive carrying the probe's raw message, so the user
    (or a developer reading audit logs) can see what is actually failing
    rather than a generic "no problem found"

Deliberately ungated by `default_route_bypasses_captive`, unlike its sibling
network probes (`RedirectLoopProbe`, `HttpsOnlyProbe`): this is the one probe
whose entire purpose is to test the default route. A `Healthy` result here
when the default route bypasses the captive network is itself meaningful —
it tells the user "your unbound path works, which is why you can browse but
the portal won't open" — so do not add a `default_route_not_captive_report`
guard to "fix" this into consistency with the other network probes.

Context-only — no network access; the actual probe call was already made by
whoever built the `ProbeContext` (`gatepath.diag_context`), and `active_probe`
just replays that cached result.
"""

from __future__ import annotations

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import DiagnosticReport, Healthy, Inconclusive


class HttpProbe:
    name = "http"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        result = ctx.active_probe()
        if result.status in ("validated", "portal"):
            return Healthy()
        return Inconclusive(probe_errors=(f"http probe: {result.message}",))
