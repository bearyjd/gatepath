"""Orchestrator for the desktop diagnostic battery.

Shares its budgets (D3: 5s total, 2s per probe) and severity table with
Android `DiagnosticEngine.kt`, but the budget *mechanism* does not mirror it.
Kotlin applies `withTimeout(perProbeBudgetMs)` to each probe individually via
coroutine cancellation, so its worst case is ~2s regardless of probe count.
This engine instead computes a single aggregate deadline — the smaller of
the total budget and (per-probe budget * probe count) — that scales with
probe count, because Python threads cannot be cancelled the way coroutines
can; see the class docstring for what that means for hung probes. [_RANK]
is the single source of truth for ordering — the UI renders, it must never
re-rank.

Concurrency is a stdlib thread pool rather than asyncio: the repo's desktop
code is threading-based throughout (`portal_monitor.Monitor`,
`DesktopIsolation`), probes are blocking I/O, and asyncio is not in the
test toolchain.
"""

from __future__ import annotations

import concurrent.futures
import dataclasses
import logging
from typing import Sequence

from gatepath.diag.probe import Probe, ProbeContext
from gatepath.diag.report import (
    ActionId,
    Cause,
    DiagnosticReport,
    Healthy,
    Inconclusive,
    NO_ACTION,
    RecommendedAction,
)

logger = logging.getLogger(__name__)

# Severity table. Mirrors DiagnosticEngine.rankOf exactly; the two must not
# drift (PR 5 adds a cross-platform guard over the cause vocabulary).
_RANK: dict[Cause, int] = {
    Cause.VPN_BLOCKING: 100,
    Cause.DNS_HIJACK: 90,
    Cause.NO_DNS_SERVERS: 85,
    Cause.HTTP_PROXY_BLOCKING: 70,
    Cause.PORTAL_REDIRECT_LOOP: 65,
    Cause.CLOCK_SKEW: 55,
    Cause.HTTPS_ONLY_CAPTIVE: 40,
    Cause.INCONCLUSIVE: 10,
    Cause.HEALTHY: 0,
}


@dataclasses.dataclass(frozen=True)
class ProbeCheck:
    """One probe's named outcome."""

    probe_name: str
    report: DiagnosticReport


@dataclasses.dataclass(frozen=True)
class DiagnosisResult:
    """Top finding plus every probe's named outcome."""

    top: DiagnosticReport
    checks: tuple[ProbeCheck, ...]
    recommended: RecommendedAction


def _recommended_action_for(report: DiagnosticReport) -> RecommendedAction:
    if report.cause is Cause.VPN_BLOCKING:
        return RecommendedAction(
            action_id=ActionId.PAUSE_VPN,
            instruction=(
                f"Your VPN ({report.interface_name}) is blocking captive sign-in. "
                "Pause it, sign in, then re-enable."
            ),
        )
    if report.cause is Cause.HTTP_PROXY_BLOCKING:
        return RecommendedAction(
            action_id=ActionId.DISABLE_HTTP_PROXY,
            instruction=(
                f"An HTTP proxy ({report.description}) is intercepting the captive "
                "redirect. Disable it for this network."
            ),
        )
    if report.cause is Cause.NO_DNS_SERVERS:
        return RecommendedAction(
            action_id=ActionId.RECONNECT_NETWORK,
            instruction=(
                "This network gave no DNS servers — the connection is half-broken. "
                "Reconnect to the network."
            ),
        )
    if report.cause is Cause.PORTAL_REDIRECT_LOOP:
        return RecommendedAction(
            action_id=ActionId.RECONNECT_NETWORK,
            instruction=(
                f"The sign-in page is stuck in a redirect loop ({len(report.chain)} hops). "
                "Reconnect to the network."
            ),
        )
    if report.cause is Cause.CLOCK_SKEW:
        return RecommendedAction(
            action_id=ActionId.OPEN_DATE_TIME_SETTINGS,
            instruction=(
                f"Your clock is off by about {report.skew_seconds // 60} minutes, which "
                "breaks secure connections to the portal. Enable automatic date & time."
            ),
        )
    return NO_ACTION


class DiagnosticEngine:
    """Runs probes concurrently under a wall-clock budget, then ranks them.

    The wall-clock budget bounds how long `run()` *waits*, not how long a
    probe thread actually runs. Python cannot kill a thread: probes that
    overrun the deadline are abandoned, not cancelled. They keep running in
    the background and their eventual results are discarded. This is why
    every I/O capability injected through `ProbeContext` must carry its own
    timeout — the engine bounds the caller's wait, not the probe's
    lifetime.
    """

    def __init__(
        self,
        probes: Sequence[Probe],
        total_budget_seconds: float = 5.0,
        per_probe_budget_seconds: float = 2.0,
    ) -> None:
        self._probes = tuple(probes)
        self._total_budget = total_budget_seconds
        self._per_probe_budget = per_probe_budget_seconds

    def run(self, ctx: ProbeContext) -> DiagnosisResult:
        if not self._probes:
            return DiagnosisResult(top=Healthy(), checks=(), recommended=NO_ACTION)

        reports: list[DiagnosticReport] = [Healthy()] * len(self._probes)

        # Not a `with` block: ThreadPoolExecutor.__exit__ calls
        # shutdown(wait=True), which blocks until every submitted thread
        # finishes — including ones already reported as timed out below.
        # future.cancel() only works on futures that never started running,
        # so a genuinely hung probe would make run() block indefinitely
        # despite the deadline. shutdown(wait=False, cancel_futures=True) in
        # the finally lets run() return as soon as the deadline passes;
        # overrun threads are abandoned (see class docstring), not killed.
        pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=len(self._probes),
            thread_name_prefix="gatepath-diag",
        )
        try:
            futures = {
                pool.submit(probe.run, ctx): index
                for index, probe in enumerate(self._probes)
            }
            # concurrent.futures has no per-future timeout, so we enforce a
            # single wall-clock deadline: the smaller of the total budget and
            # (per-probe budget * probe count). This keeps a slow probe from
            # ever stalling the whole battery beyond what its own budget,
            # scaled across the batch, would allow — while still respecting
            # the total budget as a hard ceiling.
            deadline = min(self._total_budget, self._per_probe_budget * len(self._probes))
            done, not_done = concurrent.futures.wait(futures, timeout=deadline)
            for future in done:
                index = futures[future]
                name = self._probes[index].name
                try:
                    reports[index] = future.result(timeout=0)
                except Exception as exc:  # noqa: BLE001 — one probe must not kill the run.
                    # Deliberately Exception, not BaseException: a
                    # KeyboardInterrupt (or SystemExit) raised inside a probe
                    # should propagate and tear down the process, not be
                    # swallowed into an Inconclusive finding.
                    logger.warning("Probe %s failed: %s", name, exc)
                    reports[index] = Inconclusive(probe_errors=(f"{name}: {exc}",))
            for future in not_done:
                index = futures[future]
                name = self._probes[index].name
                future.cancel()
                reports[index] = Inconclusive(probe_errors=(f"{name}: exceeded the diagnostic budget",))
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        checks = tuple(
            ProbeCheck(probe_name=probe.name, report=reports[index])
            for index, probe in enumerate(self._probes)
        )
        findings = [r for r in reports if r.cause is not Cause.HEALTHY]
        top: DiagnosticReport = (
            max(findings, key=lambda r: _RANK[r.cause]) if findings else Healthy()
        )
        return DiagnosisResult(top=top, checks=checks, recommended=_recommended_action_for(top))
