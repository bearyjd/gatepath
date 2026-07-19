"""Detects a captive gateway whose sign-in redirect chain loops forever.

Follows the redirect chain from `ProbeContext.probe_url` via
`ProbeContext.http_fetch` (one no-follow GET per hop) and reports
`PortalRedirectLoop` when a URL repeats — a looping gateway leaves the user
staring at a spinner with no page to sign in on.

A chain that terminates (204, page, error mid-chain) or simply runs past
`MAX_HOPS` without repeating is not a loop: Healthy. Only a failure of the
very first fetch is Inconclusive — mid-chain errors mean the gateway is
serving *something*, which other probes judge better.

Mirror of Android `RedirectLoopProbe.kt`.
"""

from __future__ import annotations

import urllib.parse

from gatepath.diag.probe import ProbeContext
from gatepath.diag.report import DiagnosticReport, Healthy, Inconclusive, PortalRedirectLoop

# Redirect hops to follow before concluding "long but not looping".
MAX_HOPS = 5


class RedirectLoopProbe:
    name = "redirect_loop"

    def run(self, ctx: ProbeContext) -> DiagnosticReport:
        visited = [ctx.probe_url]
        current = ctx.probe_url
        for hop in range(MAX_HOPS):
            result = ctx.http_fetch(current, None)
            if result.error is not None:
                if hop == 0:
                    return Inconclusive(probe_errors=(f"redirect_loop: {result.error}",))
                return Healthy()
            status = result.status_code
            if status is None or not (300 <= status <= 399):
                return Healthy()
            location = result.location
            if location is None:
                return Healthy()
            next_url = urllib.parse.urljoin(current, location)
            visited.append(next_url)
            if next_url in visited[:-1]:
                return PortalRedirectLoop(chain=tuple(visited))
            current = next_url
        return Healthy()
