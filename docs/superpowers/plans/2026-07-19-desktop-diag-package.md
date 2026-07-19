# Desktop Diagnostics Package Implementation Plan (PR 3 of 5)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `desktop/gatepath/diag/` — a pure, stdlib-only mirror of the Android diagnostics engine — plus the supporting HTTP fetcher and context assembler. No UI (that is PR 4).

**Architecture:** Same split the Android side settled on: `diag/` is **pure** (no I/O, no `urllib`, no `dasbus`, no `gi`), driven by values and callables injected through a frozen `ProbeContext`. All platform reads live outside the package — `gatepath/http_fetcher.py` for HTTP, `gatepath/diag_context.py` for assembling a context from the real system. Concurrency is `concurrent.futures.ThreadPoolExecutor` (stdlib, matches the repo's threading-not-asyncio posture) under a 5s total / 2s per-probe budget.

**Tech Stack:** Python 3.11+, stdlib only inside `desktop/gatepath/`, pytest, the existing `mock_portal` conftest fixture.

**Spec:** `docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md` (PR 3 scope). The Android engine has evolved past the spec's sketch since it was written — this plan mirrors the **merged** Android shape (`main` @ `d93f3f6`), which adds `ProbeCheck`, the `NoDnsServers`/`PortalRedirectLoop`/`ClockSkew` causes, and capability injection.

## Design decisions (made at plan time; deviations from the spec sketch are recorded)

1. **`diag/` stays pure.** The spec listed `proxy_probe` reading "`http(s)_proxy` env + GNOME proxy settings" and `no_dns_probe` reading "resolv.conf / NetworkManager". Doing that inside a probe would put `os.environ`/file/D-Bus reads in the pure package and drag `gi` into it. Instead the *values* arrive on `ProbeContext` and `gatepath/diag_context.py` does the reading — exactly how Android's `CaptivePortalMonitor.buildDiagnostics` feeds its pure `ProbeContext`.
2. **Desktop DOES need `default_route_bypasses_captive` — decision reversed mid-PR.** The original call was that Android added the gate because of its bound-vs-unbound socket distinction, which desktop lacks. Task 7's review proved that reasoning confused mechanism with consequence: the *failure mode* is reachable on desktop too. Diagnostic fetches use plain `urllib` with no route confinement, so with a split-tunnel VPN the probes can reach the real internet and report `Healthy` for a captive path they never touched. Nothing catches it except `VpnProbe` outranking them — and that leans on the interface-prefix heuristic this repo already got burned by (#73). Desktop derives the flag differently from Android: **NetworkManager says the interface is captive while the connectivity probe returns validated** is the same contradiction Android detects via its fallback probe. Derived in `diag_context.build_probe_context` (Task 9), consumed by the same three probes.
3. **Desktop cause vocabulary is 9 of the 12.** `PrivateDnsBlocking`, `CellularFallback` and `SandboxedWebView` are Android-only concepts. PR 5's parity guard encodes that allowlist.
4. **`_is_private_or_loopback` mirrors Android's IPv4-only limitation deliberately**, so the two engines behave identically. Widening both to IPv6/ULA/CGNAT is a cross-platform follow-up, not this PR — a silent behavioural divergence between mirrored engines would be worse than a shared, documented gap.
5. **`vpn_detector` gains a public structured accessor.** `detect_vpn_interfaces()` returns `list[str]` labels; the probe needs name + mode separately. Add `detect_vpn_details() -> list[VpnInterface]` and make the existing function a thin wrapper over it — no behaviour change for existing callers.
6. **netns `RefusalReason` is NOT mapped into a diagnostic cause.** It answers "why did the helper refuse a D-Bus call", not "why won't the portal work". Recorded as future work.

## Global Constraints

- Branch `feat/desktop-diag-package` off `main` (@ `d93f3f6`). Land via reviewed PR; never push to `main`.
- **Nothing under `desktop/gatepath/diag/` may import** `urllib`, `socket`, `os`, `dasbus`, `gi`, or read files. It is pure logic over injected data. (`gatepath/http_fetcher.py` and `gatepath/diag_context.py` are outside the package and may.)
- CI forbids `requests`/`httpx`/`aiohttp` anywhere in `desktop/gatepath/` (`.github/workflows/desktop.yml` job `forbidden-imports`).
- Immutability: every dataclass `frozen=True`; collections on the context are tuples, not lists.
- Cause names must match the Kotlin sealed-variant names **exactly** (`Healthy`, `VpnBlocking`, `DnsHijack`, `HttpProxyBlocking`, `HttpsOnlyCaptive`, `NoDnsServers`, `PortalRedirectLoop`, `ClockSkew`, `Inconclusive`) — PR 5's guard string-matches them.
- Severity ranks mirror `DiagnosticEngine.rankOf` exactly: VpnBlocking 100, DnsHijack 90, NoDnsServers 85, HttpProxyBlocking 70, PortalRedirectLoop 65, ClockSkew 55, HttpsOnlyCaptive 40, Inconclusive 10, Healthy 0.
- Test command: `python -m pytest desktop/ mockportal/ -q` from repo root. Baseline **277**. Every task states an expected total, but the **delta** is the binding number — Task 3's fix added a regression test, shifting every later absolute by +1. If the absolute disagrees with what you observe, trust the delta, report both, and do not invent tests to hit a number.
- Type annotations on all signatures; `from __future__ import annotations` at the top of each new module (matches existing files).
- Commit format `<type>: <description>`, no attribution.

---

### Task 1: `diag/report.py` — cause vocabulary and result types

**Files:**
- Create: `desktop/gatepath/diag/__init__.py` (empty)
- Create: `desktop/gatepath/diag/report.py`
- Test: `desktop/tests/test_diag_report.py`

**Interfaces:**
- Produces: `Cause` (str enum, 9 members); frozen report dataclasses `Healthy`, `VpnBlocking(interface_name, is_full_tunnel)`, `DnsHijack(host_probed, system_answer, doh_answer)`, `HttpProxyBlocking(description)`, `HttpsOnlyCaptive(https_error_message)`, `NoDnsServers`, `PortalRedirectLoop(chain: tuple[str, ...])`, `ClockSkew(skew_seconds: int)`, `Inconclusive(probe_errors: tuple[str, ...])`; each carries `cause: ClassVar[Cause]`. Union alias `DiagnosticReport`. Also `RecommendedAction` (frozen: `action_id: str | None`, `instruction: str | None`; `NO_ACTION` singleton) and `ActionId` constants mirroring Kotlin's `RecommendedAction.Ids`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the desktop diagnostic cause vocabulary."""
from __future__ import annotations

import dataclasses

import pytest

from gatepath.diag.report import (
    Cause,
    ClockSkew,
    DnsHijack,
    Healthy,
    HttpProxyBlocking,
    HttpsOnlyCaptive,
    Inconclusive,
    NoDnsServers,
    PortalRedirectLoop,
    VpnBlocking,
)

ALL_REPORTS = [
    Healthy(),
    VpnBlocking(interface_name="tun0", is_full_tunnel=True),
    DnsHijack(host_probed="example.test", system_answer="192.168.1.1", doh_answer="93.184.216.34"),
    HttpProxyBlocking(description="proxy.corp:3128"),
    HttpsOnlyCaptive(https_error_message="connection reset"),
    NoDnsServers(),
    PortalRedirectLoop(chain=("http://a", "http://b", "http://a")),
    ClockSkew(skew_seconds=900),
    Inconclusive(probe_errors=("vpn: boom",)),
]


def test_every_report_exposes_a_cause() -> None:
    for report in ALL_REPORTS:
        assert isinstance(report.cause, Cause)


def test_cause_values_match_the_kotlin_variant_names() -> None:
    # PR 5's parity guard string-matches these against the Kotlin sealed
    # interface, so the spelling is a contract, not a label.
    assert {c.value for c in Cause} == {
        "Healthy",
        "VpnBlocking",
        "DnsHijack",
        "HttpProxyBlocking",
        "HttpsOnlyCaptive",
        "NoDnsServers",
        "PortalRedirectLoop",
        "ClockSkew",
        "Inconclusive",
    }


def test_every_cause_has_exactly_one_report_type() -> None:
    seen = [r.cause for r in ALL_REPORTS]
    assert sorted(c.value for c in seen) == sorted(c.value for c in Cause)


def test_reports_are_immutable() -> None:
    report = VpnBlocking(interface_name="tun0", is_full_tunnel=False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        report.interface_name = "wg0"  # type: ignore[misc]
```

- [ ] **Step 2: Run to verify RED**

Run: `python -m pytest desktop/tests/test_diag_report.py -q`
Expected: collection error — `ModuleNotFoundError: No module named 'gatepath.diag'`.

- [ ] **Step 3: Implement**

Create empty `desktop/gatepath/diag/__init__.py`, then `desktop/gatepath/diag/report.py`:

```python
"""Diagnostic cause vocabulary for the desktop engine.

Mirror of the Android sealed `DiagnosticReport` hierarchy
(`android/app/src/main/java/com/ventouxlabs/gatepath/diag/DiagnosticReport.kt`).
The [Cause] values are spelled exactly as the Kotlin variant names because
PR 5's cross-platform parity guard string-matches them — treat the spelling
as a wire contract, not a label.

Desktop legitimately lacks three Android causes: `PrivateDnsBlocking`
(Android system Private DNS), `CellularFallback` (no cellular), and
`SandboxedWebView` (Android WebView process model). The parity guard
encodes that allowlist.

Pure module: no I/O, no platform imports.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import ClassVar, Optional, Union


class Cause(str, enum.Enum):
    """One diagnosed cause. Values mirror the Kotlin variant names."""

    HEALTHY = "Healthy"
    VPN_BLOCKING = "VpnBlocking"
    DNS_HIJACK = "DnsHijack"
    HTTP_PROXY_BLOCKING = "HttpProxyBlocking"
    HTTPS_ONLY_CAPTIVE = "HttpsOnlyCaptive"
    NO_DNS_SERVERS = "NoDnsServers"
    PORTAL_REDIRECT_LOOP = "PortalRedirectLoop"
    CLOCK_SKEW = "ClockSkew"
    INCONCLUSIVE = "Inconclusive"


@dataclasses.dataclass(frozen=True)
class Healthy:
    """Probe ran cleanly and saw no problem on its dimension."""

    cause: ClassVar[Cause] = Cause.HEALTHY


@dataclasses.dataclass(frozen=True)
class VpnBlocking:
    """A VPN is up and is the likely reason captive sign-in cannot complete."""

    interface_name: str
    is_full_tunnel: bool
    cause: ClassVar[Cause] = Cause.VPN_BLOCKING


@dataclasses.dataclass(frozen=True)
class DnsHijack:
    """System resolver and an independent resolver disagree for the same host."""

    host_probed: str
    system_answer: str
    doh_answer: str
    cause: ClassVar[Cause] = Cause.DNS_HIJACK


@dataclasses.dataclass(frozen=True)
class HttpProxyBlocking:
    """An HTTP proxy is configured and is eating the captive redirect."""

    description: str
    cause: ClassVar[Cause] = Cause.HTTP_PROXY_BLOCKING


@dataclasses.dataclass(frozen=True)
class HttpsOnlyCaptive:
    """Cleartext HTTP works but HTTPS is blocked or intercepted."""

    https_error_message: str
    cause: ClassVar[Cause] = Cause.HTTPS_ONLY_CAPTIVE


@dataclasses.dataclass(frozen=True)
class NoDnsServers:
    """DHCP handed this network zero DNS servers — a half-broken connect."""

    cause: ClassVar[Cause] = Cause.NO_DNS_SERVERS


@dataclasses.dataclass(frozen=True)
class PortalRedirectLoop:
    """The sign-in redirect chain revisits a URL it already issued."""

    chain: tuple[str, ...]
    cause: ClassVar[Cause] = Cause.PORTAL_REDIRECT_LOOP


@dataclasses.dataclass(frozen=True)
class ClockSkew:
    """Device clock disagrees with the gateway's Date header beyond tolerance."""

    skew_seconds: int
    cause: ClassVar[Cause] = Cause.CLOCK_SKEW


@dataclasses.dataclass(frozen=True)
class Inconclusive:
    """No finding; carries the raw probe errors so a human can read them."""

    probe_errors: tuple[str, ...]
    cause: ClassVar[Cause] = Cause.INCONCLUSIVE


DiagnosticReport = Union[
    Healthy,
    VpnBlocking,
    DnsHijack,
    HttpProxyBlocking,
    HttpsOnlyCaptive,
    NoDnsServers,
    PortalRedirectLoop,
    ClockSkew,
    Inconclusive,
]


class ActionId:
    """Action identifiers. Mirrors Kotlin `RecommendedAction.Ids`.

    Per D1 the engine never applies a fix — it names one, and the UI layer
    decides how to surface it.
    """

    PAUSE_VPN = "pause_vpn"
    DISABLE_HTTP_PROXY = "disable_http_proxy"
    RECONNECT_NETWORK = "reconnect_network"
    OPEN_DATE_TIME_SETTINGS = "open_date_time_settings"


@dataclasses.dataclass(frozen=True)
class RecommendedAction:
    """A step the user must take. Both fields None means 'nothing actionable'."""

    action_id: Optional[str] = None
    instruction: Optional[str] = None


NO_ACTION = RecommendedAction()
```

- [ ] **Step 4: Run to verify GREEN**

Run: `python -m pytest desktop/ mockportal/ -q`
Expected: PASS, **281** tests (277 + 4).

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/diag/ desktop/tests/test_diag_report.py
git commit -m "feat(desktop): add diagnostic cause vocabulary mirroring Android"
```

---

### Task 2: `diag/probe.py` — probe protocol and `ProbeContext`

**Files:**
- Create: `desktop/gatepath/diag/probe.py`
- Test: `desktop/tests/test_diag_probe.py`

**Interfaces:**
- Produces: `HttpFetchResult` (frozen: `status_code: int | None`, `location: str | None`, `date_epoch_seconds: float | None`, `body: str | None`, `error: str | None`); `ProbeContext` (frozen) with fields `interface_name: str`, `probe_url: str`, `vpn_interfaces: tuple[VpnDetail, ...]`, `http_proxy_description: str | None`, `dns_server_count: int`, `http_fetch: Callable[[str, Optional[str]], HttpFetchResult]`, `resolve_host: Callable[[str], tuple[str, ...]]`, `now_epoch_seconds: Callable[[], float]`, `active_probe: Callable[[], ProbeResult]`; `VpnDetail` (frozen: `name: str`, `is_full_tunnel: bool`); `Probe` Protocol with `name: str` and `run(ctx) -> DiagnosticReport`.

`HttpFetchResult` lives here (not in the fetcher module) so `diag/` never imports the I/O module — the fetcher imports *this*, inverting the dependency exactly as Android's pure `ProbeContext` references `ProbeResult`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the diagnostic probe protocol and context."""
from __future__ import annotations

import dataclasses

import pytest

from gatepath.diag.probe import HttpFetchResult, ProbeContext, VpnDetail
from gatepath.diag.report import Healthy


def make_context(**overrides: object) -> ProbeContext:
    defaults: dict[str, object] = {
        "interface_name": "wlan0",
        "probe_url": "http://portal.test/probe",
        "vpn_interfaces": (),
        "http_proxy_description": None,
        "dns_server_count": 1,
        "http_fetch": lambda url, accept: HttpFetchResult(None, None, None, None, "not wired"),
        "resolve_host": lambda host: (),
        "now_epoch_seconds": lambda: 0.0,
        "active_probe": lambda: None,
    }
    defaults.update(overrides)
    return ProbeContext(**defaults)  # type: ignore[arg-type]


def test_context_is_immutable() -> None:
    ctx = make_context()
    with pytest.raises(dataclasses.FrozenInstanceError):
        ctx.interface_name = "eth0"  # type: ignore[misc]


def test_context_defaults_are_inert() -> None:
    ctx = make_context()
    assert ctx.http_fetch("http://x", None).error == "not wired"
    assert ctx.resolve_host("x") == ()


def test_vpn_detail_carries_name_and_tunnel_mode() -> None:
    detail = VpnDetail(name="tun0", is_full_tunnel=True)
    assert detail.name == "tun0"
    assert detail.is_full_tunnel is True


def test_a_probe_satisfies_the_protocol_structurally() -> None:
    from gatepath.diag.probe import Probe

    class Stub:
        name = "stub"

        def run(self, ctx: ProbeContext) -> Healthy:
            return Healthy()

    probe: Probe = Stub()
    assert probe.run(make_context()).cause.value == "Healthy"
```

- [ ] **Step 2: RED** — `python -m pytest desktop/tests/test_diag_probe.py -q`; expected `ModuleNotFoundError: gatepath.diag.probe`.

- [ ] **Step 3: Implement** `desktop/gatepath/diag/probe.py`:

```python
"""Probe protocol and the immutable context handed to every probe.

Mirror of Android `DiagnosticProbe.kt` + `ProbeContext.kt`. The context is
pure data plus injected callables, so every probe is directly unit-testable
with fakes and the package needs no I/O imports. Whoever runs the engine
(`gatepath.diag_context`) is responsible for filling these in from the real
system.

Deliberately absent versus Android: `is_private_dns_active` (an Android
system setting), `has_validated_cellular` (no cellular), and
`default_route_bypasses_captive` (Android has a bound-vs-unbound socket
distinction; desktop isolates with netns at the OS level and has no
per-request binding to bypass).
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Callable, Optional, Protocol

from gatepath.diag.report import DiagnosticReport

if TYPE_CHECKING:
    # Type-only import: erased at runtime, so `diag/` keeps its no-platform-import
    # guarantee while `active_probe` stays a properly typed seam rather than `Any`.
    from gatepath.portal_probe import ProbeResult


@dataclasses.dataclass(frozen=True)
class HttpFetchResult:
    """Outcome of one no-follow GET. `error` is set iff no status was obtained.

    Declared here rather than beside the fetcher so `diag/` never imports the
    I/O module — the fetcher depends on this package, not the reverse.
    """

    status_code: Optional[int]
    location: Optional[str]
    date_epoch_seconds: Optional[float]
    body: Optional[str]
    error: Optional[str]


@dataclasses.dataclass(frozen=True)
class VpnDetail:
    """A detected VPN interface, split into the parts a probe reports on."""

    name: str
    is_full_tunnel: bool


@dataclasses.dataclass(frozen=True)
class ProbeContext:
    """Immutable snapshot of network state plus injected capabilities."""

    interface_name: str
    probe_url: str
    vpn_interfaces: tuple[VpnDetail, ...]
    http_proxy_description: Optional[str]
    dns_server_count: int
    http_fetch: Callable[[str, Optional[str]], HttpFetchResult]
    resolve_host: Callable[[str], tuple[str, ...]]
    now_epoch_seconds: Callable[[], float]
    active_probe: Callable[[], "ProbeResult"]


class Probe(Protocol):
    """One axis of captive-portal diagnosis.

    Probes are stateless, must not mutate the context, and must complete
    inside the engine's per-probe budget. Network access goes through the
    context's injected callables, never directly.
    """

    name: str

    def run(self, ctx: ProbeContext) -> DiagnosticReport: ...
```

- [ ] **Step 4: GREEN** — `python -m pytest desktop/ mockportal/ -q`; expected **285**.

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/diag/probe.py desktop/tests/test_diag_probe.py
git commit -m "feat(desktop): add diagnostic probe protocol and immutable context"
```

---

### Task 3: `diag/engine.py` — concurrent battery with budgets and ranking

**Files:**
- Create: `desktop/gatepath/diag/engine.py`
- Test: `desktop/tests/test_diag_engine.py`

**Interfaces:**
- Produces: `ProbeCheck` (frozen: `probe_name: str`, `report: DiagnosticReport`); `DiagnosisResult` (frozen: `top: DiagnosticReport`, `checks: tuple[ProbeCheck, ...]`, `recommended: RecommendedAction`); `DiagnosticEngine(probes, total_budget_seconds=5.0, per_probe_budget_seconds=2.0)` with `run(ctx) -> DiagnosisResult`; module-level `_RANK: dict[Cause, int]`.

Ranking, per-probe failure→`Inconclusive`, and deadline handling mirror `DiagnosticEngine.kt`. `_RANK` is the single severity authority; nothing else re-ranks.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the desktop diagnostic engine."""
from __future__ import annotations

import time

from gatepath.diag.engine import DiagnosticEngine
from gatepath.diag.probe import HttpFetchResult, ProbeContext
from gatepath.diag.report import (
    ActionId,
    Cause,
    Healthy,
    HttpProxyBlocking,
    NoDnsServers,
    VpnBlocking,
)

NOOP_CTX = ProbeContext(
    interface_name="wlan0",
    probe_url="http://portal.test/probe",
    vpn_interfaces=(),
    http_proxy_description=None,
    dns_server_count=1,
    http_fetch=lambda url, accept: HttpFetchResult(None, None, None, None, "not wired"),
    resolve_host=lambda host: (),
    now_epoch_seconds=lambda: 0.0,
    active_probe=lambda: None,
)


class StubProbe:
    def __init__(self, name: str, report: object, delay: float = 0.0) -> None:
        self.name = name
        self._report = report
        self._delay = delay

    def run(self, ctx: ProbeContext) -> object:
        if self._delay:
            time.sleep(self._delay)
        return self._report


class ExplodingProbe:
    name = "boom"

    def run(self, ctx: ProbeContext) -> object:
        raise RuntimeError("probe exploded")


def test_all_healthy_yields_healthy_and_no_action() -> None:
    engine = DiagnosticEngine([StubProbe("a", Healthy()), StubProbe("b", Healthy())])
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.HEALTHY
    assert result.recommended.action_id is None


def test_top_finding_is_the_highest_ranked_report() -> None:
    engine = DiagnosticEngine(
        [
            StubProbe("proxy", HttpProxyBlocking(description="p:3128")),
            StubProbe("vpn", VpnBlocking(interface_name="tun0", is_full_tunnel=True)),
            StubProbe("ok", Healthy()),
        ]
    )
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.VPN_BLOCKING
    assert result.recommended.action_id == ActionId.PAUSE_VPN


def test_no_dns_outranks_http_proxy() -> None:
    engine = DiagnosticEngine(
        [
            StubProbe("proxy", HttpProxyBlocking(description="p:3128")),
            StubProbe("nodns", NoDnsServers()),
        ]
    )
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.NO_DNS_SERVERS
    assert result.recommended.action_id == ActionId.RECONNECT_NETWORK


def test_checks_carry_probe_names_in_probe_list_order() -> None:
    engine = DiagnosticEngine(
        [StubProbe("vpn", VpnBlocking(interface_name="tun0", is_full_tunnel=True)), StubProbe("ok", Healthy())]
    )
    result = engine.run(NOOP_CTX)
    assert [c.probe_name for c in result.checks] == ["vpn", "ok"]


def test_a_raising_probe_becomes_inconclusive_without_killing_the_run() -> None:
    engine = DiagnosticEngine([ExplodingProbe(), StubProbe("ok", Healthy())])
    result = engine.run(NOOP_CTX)
    boom = next(c for c in result.checks if c.probe_name == "boom")
    assert boom.report.cause is Cause.INCONCLUSIVE
    assert "probe exploded" in boom.report.probe_errors[0]


def test_a_probe_over_its_budget_becomes_inconclusive() -> None:
    engine = DiagnosticEngine(
        [StubProbe("slow", Healthy(), delay=0.5), StubProbe("fast", Healthy())],
        total_budget_seconds=1.0,
        per_probe_budget_seconds=0.1,
    )
    result = engine.run(NOOP_CTX)
    slow = next(c for c in result.checks if c.probe_name == "slow")
    assert slow.report.cause is Cause.INCONCLUSIVE
    fast = next(c for c in result.checks if c.probe_name == "fast")
    assert fast.report.cause is Cause.HEALTHY


def test_inconclusive_ranks_below_every_real_finding() -> None:
    engine = DiagnosticEngine([ExplodingProbe(), StubProbe("proxy", HttpProxyBlocking(description="p:3128"))])
    result = engine.run(NOOP_CTX)
    assert result.top.cause is Cause.HTTP_PROXY_BLOCKING
```

- [ ] **Step 2: RED** — expected `ModuleNotFoundError: gatepath.diag.engine`.

- [ ] **Step 3: Implement** `desktop/gatepath/diag/engine.py`:

```python
"""Orchestrator for the desktop diagnostic battery.

Mirror of Android `DiagnosticEngine.kt`, including its budgets (D3: 5s total,
2s per probe) and its severity table. [_RANK] is the single source of truth
for ordering — the UI renders, it must never re-rank.

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
    """Runs probes concurrently under a wall-clock budget, then ranks them."""

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
        reports: list[DiagnosticReport] = [Healthy()] * len(self._probes)
        if not self._probes:
            return DiagnosisResult(top=Healthy(), checks=(), recommended=NO_ACTION)

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(self._probes),
            thread_name_prefix="gatepath-diag",
        ) as pool:
            futures = {
                pool.submit(probe.run, ctx): index
                for index, probe in enumerate(self._probes)
            }
            deadline_budget = min(self._total_budget, self._per_probe_budget * len(self._probes))
            done, not_done = concurrent.futures.wait(
                futures,
                timeout=min(self._total_budget, deadline_budget),
            )
            for future in done:
                index = futures[future]
                name = self._probes[index].name
                try:
                    reports[index] = future.result(timeout=0)
                except Exception as exc:  # noqa: BLE001 — one probe must not kill the run
                    logger.warning("Probe %s failed: %s", name, exc)
                    reports[index] = Inconclusive(probe_errors=(f"{name}: {exc}",))
            for future in not_done:
                index = futures[future]
                name = self._probes[index].name
                future.cancel()
                reports[index] = Inconclusive(probe_errors=(f"{name}: exceeded the diagnostic budget",))

        checks = tuple(
            ProbeCheck(probe_name=probe.name, report=reports[index])
            for index, probe in enumerate(self._probes)
        )
        findings = [r for r in reports if r.cause is not Cause.HEALTHY]
        top: DiagnosticReport = (
            max(findings, key=lambda r: _RANK[r.cause]) if findings else Healthy()
        )
        return DiagnosisResult(top=top, checks=checks, recommended=_recommended_action_for(top))
```

**Note on the per-probe budget:** `concurrent.futures` has no per-future timeout, so this enforces the *total* deadline and attributes overruns per probe. The `test_a_probe_over_its_budget_becomes_inconclusive` case pins the behaviour that matters (a slow probe does not stall the battery and is reported honestly). If that test does not pass with the code above, adjust the deadline computation — do **not** weaken the test.

- [ ] **Step 4: GREEN** — `python -m pytest desktop/ mockportal/ -q`; expected **292**.

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/diag/engine.py desktop/tests/test_diag_engine.py
git commit -m "feat(desktop): add diagnostic engine with budgets and severity ranking"
```

---

### Task 4: `vpn_detector` structured accessor

**Files:**
- Modify: `desktop/gatepath/vpn_detector.py`
- Test: `desktop/tests/test_vpn_detector.py` (append)

**Interfaces:**
- Produces: `detect_vpn_details(_open=urllib.request.urlopen) -> list[VpnInterface]`. `detect_vpn_interfaces` becomes a thin wrapper returning `[v.label() for v in detect_vpn_details(_open)]` — identical output, so every existing caller and the audit-log format are untouched.

- [ ] **Step 1: Write the failing test** — append to `desktop/tests/test_vpn_detector.py`, matching its existing `MagicMock`/`patch` fixture style:

```python
def test_detect_vpn_details_returns_structured_interfaces() -> None:
    with patch("socket.if_nameindex", return_value=[(1, "lo"), (2, "tun0")]):
        details = vpn_detector.detect_vpn_details()
    assert [d.name for d in details] == ["tun0"]
    assert details[0].mode == "unknown"


def test_detect_vpn_interfaces_still_returns_the_same_labels() -> None:
    # The label format is the audit-log contract — refactoring the internals
    # must not change it.
    with patch("socket.if_nameindex", return_value=[(1, "lo"), (2, "tun0")]):
        labels = vpn_detector.detect_vpn_interfaces()
        details = vpn_detector.detect_vpn_details()
    assert labels == [d.label() for d in details] == ["tun0 (unknown)"]
```

- [ ] **Step 2: RED** — expected `AttributeError: module 'gatepath.vpn_detector' has no attribute 'detect_vpn_details'`.

- [ ] **Step 3: Implement** — rename the body of `detect_vpn_interfaces` to `detect_vpn_details`, returning `results: list[VpnInterface]` (append `vpn`, not `vpn.label()`; keep the existing `logger.info` line using `vpn.label()`), then:

```python
def detect_vpn_interfaces(
    _open: Callable = urllib.request.urlopen,
) -> list[str]:
    """Return VPN interface labels in audit-log format.

    Thin wrapper over [detect_vpn_details] — the label format is the
    audit-log contract, so it stays defined in exactly one place.
    """
    return [vpn.label() for vpn in detect_vpn_details(_open)]
```

Update the module docstring if it enumerates the public functions.

- [ ] **Step 4: GREEN** — `python -m pytest desktop/ mockportal/ -q`; expected **294**.

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/vpn_detector.py desktop/tests/test_vpn_detector.py
git commit -m "refactor(desktop): expose structured detect_vpn_details alongside labels"
```

---

### Task 5: Context-only probes — vpn, http_proxy, no_dns

**Files:**
- Create: `desktop/gatepath/diag/vpn_probe.py`, `desktop/gatepath/diag/http_proxy_probe.py`, `desktop/gatepath/diag/no_dns_probe.py`
- Test: `desktop/tests/test_diag_context_probes.py`

**Interfaces:**
- Produces: `VpnProbe` (`name = "vpn"`) → `VpnBlocking(interface_name, is_full_tunnel)` from the first entry of `ctx.vpn_interfaces`, else `Healthy`; `HttpProxyProbe` (`name = "http_proxy"`) → `HttpProxyBlocking(description)` when `ctx.http_proxy_description` is not None, else `Healthy`; `NoDnsProbe` (`name = "no_dns"`) → `NoDnsServers()` when `ctx.dns_server_count == 0`, else `Healthy`.

Mirrors the Android trio. One concern per file, no network access.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the context-only desktop probes."""
from __future__ import annotations

from gatepath.diag.http_proxy_probe import HttpProxyProbe
from gatepath.diag.no_dns_probe import NoDnsProbe
from gatepath.diag.probe import HttpFetchResult, ProbeContext, VpnDetail
from gatepath.diag.report import Cause
from gatepath.diag.vpn_probe import VpnProbe


def ctx(**overrides: object) -> ProbeContext:
    defaults: dict[str, object] = {
        "interface_name": "wlan0",
        "probe_url": "http://portal.test/probe",
        "vpn_interfaces": (),
        "http_proxy_description": None,
        "dns_server_count": 1,
        "http_fetch": lambda url, accept: HttpFetchResult(None, None, None, None, "unused"),
        "resolve_host": lambda host: (),
        "now_epoch_seconds": lambda: 0.0,
        "active_probe": lambda: None,
    }
    defaults.update(overrides)
    return ProbeContext(**defaults)  # type: ignore[arg-type]


def test_vpn_probe_reports_the_first_interface() -> None:
    report = VpnProbe().run(
        ctx(vpn_interfaces=(VpnDetail("tun0", False), VpnDetail("wg0", False)))
    )
    assert report.cause is Cause.VPN_BLOCKING
    assert report.interface_name == "tun0"
    assert report.is_full_tunnel is False


def test_vpn_probe_propagates_full_tunnel() -> None:
    report = VpnProbe().run(ctx(vpn_interfaces=(VpnDetail("tailscale0", True),)))
    assert report.is_full_tunnel is True


def test_vpn_probe_healthy_without_a_vpn() -> None:
    assert VpnProbe().run(ctx()).cause is Cause.HEALTHY


def test_http_proxy_probe_reports_a_configured_proxy() -> None:
    report = HttpProxyProbe().run(ctx(http_proxy_description="proxy.corp:3128"))
    assert report.cause is Cause.HTTP_PROXY_BLOCKING
    assert report.description == "proxy.corp:3128"


def test_http_proxy_probe_healthy_without_a_proxy() -> None:
    assert HttpProxyProbe().run(ctx()).cause is Cause.HEALTHY


def test_no_dns_probe_reports_zero_servers() -> None:
    assert NoDnsProbe().run(ctx(dns_server_count=0)).cause is Cause.NO_DNS_SERVERS


def test_no_dns_probe_healthy_with_servers() -> None:
    assert NoDnsProbe().run(ctx(dns_server_count=2)).cause is Cause.HEALTHY
```

- [ ] **Step 2: RED** — expected `ModuleNotFoundError` for the three probe modules.

- [ ] **Step 3: Implement** — three files, each with a module docstring explaining *why* the finding matters (match the Android probes' doc voice), e.g.:

```python
"""Reports a VPN that is likely blocking captive sign-in.

Any VPN interface is reported: even split-tunnel setups routinely install
DNS rules that break captive resolution, so the finding is worth surfacing.
`is_full_tunnel` tells the UI how certain the "pause your VPN" advice is.

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
```

`HttpProxyProbe` and `NoDnsProbe` follow the same shape against
`ctx.http_proxy_description` and `ctx.dns_server_count == 0`.

- [ ] **Step 4: GREEN** — expected **302** (+7).

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/diag/vpn_probe.py desktop/gatepath/diag/http_proxy_probe.py desktop/gatepath/diag/no_dns_probe.py desktop/tests/test_diag_context_probes.py
git commit -m "feat(desktop): add context-only vpn, proxy and no-dns probes"
```

---

### Task 6: `gatepath/http_fetcher.py` — the I/O side

**Files:**
- Create: `desktop/gatepath/http_fetcher.py`
- Test: `desktop/tests/test_http_fetcher.py`

**Interfaces:**
- Produces: `fetch(url: str, accept: str | None = None, timeout: float = 2.0) -> HttpFetchResult` (returns the `HttpFetchResult` from `gatepath.diag.probe`). No-follow redirects (reuse `portal_probe._NoFollowRedirectHandler`'s approach), parse the RFC-1123 `Date` header via `email.utils.parsedate_to_datetime`, cap the body at 64 KiB, never raise.

Mirrors Android `HttpFetcher.kt`. Lives outside `diag/` because it does I/O.

- [ ] **Step 1: Write the failing test** — use the existing `mock_portal` fixture from `desktop/tests/conftest.py` (see `test_portal_probe.py` for the idiom):

```python
def test_redirect_is_reported_not_followed(mock_portal: str) -> None:
    result = http_fetcher.fetch(f"{mock_portal}/generate_204")
    assert result.status_code == 302
    assert result.location.endswith("/portal")
    assert result.error is None


def test_date_header_is_parsed(mock_portal: str) -> None:
    result = http_fetcher.fetch(f"{mock_portal}/generate_204")
    assert result.date_epoch_seconds is not None
    assert abs(result.date_epoch_seconds - time.time()) < 60


def test_body_is_captured_for_a_page(mock_portal: str) -> None:
    result = http_fetcher.fetch(f"{mock_portal}/portal")
    assert result.status_code == 200
    assert "Test Portal" in result.body


def test_redirect_loop_endpoints_are_reported_individually(mock_portal: str) -> None:
    # PR 2 added these to the shared mock for exactly this purpose.
    result = http_fetcher.fetch(f"{mock_portal}/loop-a")
    assert result.status_code == 302
    assert result.location.endswith("/loop-b")


def test_connection_failure_becomes_an_error_never_raises() -> None:
    result = http_fetcher.fetch("http://127.0.0.1:1/nope")
    assert result.status_code is None
    assert result.error is not None
```

- [ ] **Step 2: RED** — expected `ModuleNotFoundError: gatepath.http_fetcher`.

- [ ] **Step 3: Implement.** Read `desktop/gatepath/portal_probe.py` first and follow its urllib idiom and `_NoFollowRedirectHandler` pattern. Requirements: `instanceFollowRedirects`-equivalent off (the handler returns `None` from `redirect_request`); 2s default timeout; read at most `64 * 1024` bytes and decode UTF-8 with `errors="replace"`; parse `Date` with `email.utils.parsedate_to_datetime(...).timestamp()` inside a `try` so a malformed header yields `None`; catch `urllib.error.*`, `OSError` and `ValueError` into `error`; a `HTTPError` still carries a status code and headers, so report it as a result rather than an error.

- [ ] **Step 4: GREEN** — expected **307** (+5).

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/http_fetcher.py desktop/tests/test_http_fetcher.py
git commit -m "feat(desktop): add diagnostic HTTP fetcher with Date header and capped body"
```

---

### Task 7: Network probes — redirect_loop, clock_skew, https_only

**Files:**
- Create: `desktop/gatepath/diag/redirect_loop_probe.py`, `desktop/gatepath/diag/clock_skew_probe.py`, `desktop/gatepath/diag/https_only_probe.py`
- Test: `desktop/tests/test_diag_network_probes.py`

**Interfaces:**
- `RedirectLoopProbe` (`name = "redirect_loop"`, `MAX_HOPS = 5`) → `PortalRedirectLoop(chain)` when a URL repeats, chain ending at the first repeat; `Inconclusive` only when the *first* fetch errors; `Healthy` otherwise. Relative `Location` values resolve against the current URL via `urllib.parse.urljoin`.
- `ClockSkewProbe` (`name = "clock_skew"`, tolerance 300s) → `ClockSkew(skew_seconds)` when `abs(now - date_header) > 300`; `Healthy` on a missing header or a fetch error.
- `HttpsOnlyProbe` (`name = "https_only"`) → `HttpsOnlyCaptive(error)` only when `ctx.active_probe()` reports `status == "validated"` **and** the https-scheme variant of `ctx.probe_url` errors; `Healthy` otherwise.

Mirror the merged Android probes exactly, including their verdict policies. Desktop's `ProbeResult.status` is the string `"validated"|"portal"|"error"` (see `portal_probe.py`), not a sealed type.

- [ ] **Step 1: Write the failing tests** — one file covering, at minimum: a two-node cycle detected with the exact chain; a relative-`Location` cycle; a chain ending in a page → `Healthy`; first-fetch error → `Inconclusive`; a long non-repeating chain hitting the hop cap → `Healthy`; clock skew ahead and behind both reporting positive `skew_seconds`; within-tolerance → `Healthy`; missing `Date` → `Healthy`; https-only firing only on validated-HTTP-plus-failing-HTTPS, and staying `Healthy` for portal/error HTTP verdicts. Build contexts with a dict-backed fake `http_fetch` keyed by URL, as the Android tests do.

- [ ] **Step 2: RED** — expected `ModuleNotFoundError` for the three modules.

- [ ] **Step 3: Implement** the three probes, each in its own file with a docstring explaining the failure mode and the verdict policy.

- [ ] **Step 4: GREEN** — state the observed total in the report.

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/diag/redirect_loop_probe.py desktop/gatepath/diag/clock_skew_probe.py desktop/gatepath/diag/https_only_probe.py desktop/tests/test_diag_network_probes.py
git commit -m "feat(desktop): add redirect-loop, clock-skew and https-only probes"
```

---

### Task 8: `dns_hijack_probe` with DoH

**Files:**
- Create: `desktop/gatepath/diag/dns_hijack_probe.py`
- Test: `desktop/tests/test_diag_dns_hijack_probe.py`

**Interfaces:**
- `DnsHijackProbe` (`name = "dns_hijack"`), DoH endpoint `https://1.1.1.1/dns-query`, Accept `application/dns-json`. **First statement of `run()` must be the default-route guard** (`if ctx.default_route_bypasses_captive: return default_route_not_captive_report(self.name)`), before any resolve or fetch — same as the other two network probes. Verdict policy identical to Android: host unparseable or system resolve empty → `Inconclusive`; DoH error/unparseable/no public answer → `Healthy`; `DnsHijack` only when **every** system answer is private/loopback **and** DoH returned ≥1 public address. Internal helpers `_parse_doh_addresses(body)` and `_is_private_or_loopback(address)`.

**The `1.1.1.1` IP literal is mandatory, not stylistic** — a hostname would be resolved by the very resolver under suspicion, and a fully-hijacking gateway would answer it with itself, silently blinding the probe. This exact bug was caught in Android review; do not "clean it up" into a hostname.

`_is_private_or_loopback` is IPv4-only (10/8, 172.16-31, 192.168/16, 127/8, 169.254/16), mirroring Android deliberately — see design decision 4 in this plan's header.

- [ ] **Step 1: Write the failing tests** — mirror `DnsHijackProbeTest.kt`: private system answer + public DoH answer → `DnsHijack` with the right fields; matching public answers → `Healthy`; empty system answers → `Inconclusive`; DoH unreachable → `Healthy`; malformed DoH JSON → `Healthy` (never raises); public system answer → `Healthy`. Gate the fake `http_fetch` on `accept == "application/dns-json"` so a wrong Accept header is detectable.

- [ ] **Step 2: RED** — expected `ModuleNotFoundError`.

- [ ] **Step 3: Implement.** Parse the DoH JSON with `json.loads` inside a `try` returning `()` on any problem; select `Answer` entries with `type == 1` (A records) and take their `data`.

- [ ] **Step 4: GREEN** — state the observed total.

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/diag/dns_hijack_probe.py desktop/tests/test_diag_dns_hijack_probe.py
git commit -m "feat(desktop): add DNS-hijack probe comparing system DNS against DoH"
```

---

### Task 9: `gatepath/diag_context.py` — assemble a context from the real system

**Files:**
- Create: `desktop/gatepath/diag_context.py`
- Test: `desktop/tests/test_diag_context.py`

**Interfaces:**
- Produces: `build_probe_context(interface_name, probe_url=portal_probe.CONNECTIVITY_CHECK_URL, *, environ=os.environ, resolv_conf_path="/etc/resolv.conf") -> ProbeContext` and `default_engine() -> DiagnosticEngine` (the eight-probe battery, the single place membership is declared — mirrors Android's `DiagnosticModule`).
- Also derives `default_route_bypasses_captive`: `True` when the connectivity probe returns `status == "validated"` even though NetworkManager flagged this interface captive — the desktop analogue of Android's fallback-probe contradiction. Pass it into the context.
- Helpers: `_proxy_description(environ)` reads `https_proxy`/`http_proxy` (and the uppercase spellings), returning e.g. `"proxy.corp:3128"` or `None`; `_count_dns_servers(path)` counts `nameserver` lines in resolv.conf, returning 0 when the file is missing or unreadable.

This is the platform-reading layer; keeping it out of `diag/` is what lets the package stay pure. Injecting `environ` and `resolv_conf_path` makes it testable without touching the real system. GNOME gsettings proxy reading is deliberately **not** here — it needs `gi`, and PR 4 (the GTK layer) can supply a richer description through the same field if wanted.

- [ ] **Step 1: Write the failing tests** — proxy read from `https_proxy`, from `HTTP_PROXY`, precedence when both are set, `None` when neither; DNS count from a tmp_path resolv.conf with two `nameserver` lines, 0 for a missing file, comment lines ignored; `build_probe_context` returns a context whose `vpn_interfaces` are `VpnDetail`s (patch `vpn_detector.detect_vpn_details`); `default_engine()` declares exactly the eight expected probe names.

- [ ] **Step 2: RED** — expected `ModuleNotFoundError: gatepath.diag_context`.

- [ ] **Step 3: Implement.** `http_fetch` wires to `http_fetcher.fetch`; `resolve_host` wraps `socket.getaddrinfo(host, None, family=socket.AF_INET)` returning a tuple of unique address strings and `()` on `OSError`; `now_epoch_seconds` is `time.time`; `active_probe` calls `portal_probe.probe(probe_url)`; `vpn_interfaces` maps `vpn_detector.detect_vpn_details()` to `VpnDetail(name=v.name, is_full_tunnel=v.mode == "full_tunnel")`.

- [ ] **Step 4: GREEN** — state the observed total.

- [ ] **Step 5: Commit**

```bash
git add desktop/gatepath/diag_context.py desktop/tests/test_diag_context.py
git commit -m "feat(desktop): assemble probe context and default engine from system state"
```

---

### Task 10: Final verification + PR

- [ ] **Step 1:** `python -m pytest desktop/ mockportal/ -q` — all green; state the final total (expect roughly 320).

- [ ] **Step 2:** Prove the purity constraint holds — this is the architectural invariant of the whole package:

```bash
grep -rnE '^\s*(import|from)\s+(urllib|socket|os|dasbus|gi)\b' desktop/gatepath/diag/ && echo "PURITY VIOLATION" && exit 1
echo "diag/ is pure"
```

- [ ] **Step 3:** `python -m gatepath --help` still works (PYTHONPATH=desktop) — proves no import-time regression.

- [ ] **Step 4: Push and open the PR (review-gated; do not self-merge):**

```bash
git push -u origin feat/desktop-diag-package
gh pr create --title "feat(desktop): diagnostics engine package mirroring Android" --body "$(cat <<'EOF'
## Summary
Desktop half of the diagnostics expansion — the engine, its cause vocabulary, and eight probes. No UI; that is PR 4.

- `desktop/gatepath/diag/` — pure, stdlib-only, no I/O: `report.py` (9 causes mirroring the Kotlin sealed variants), `probe.py` (protocol + immutable injected-capability context), `engine.py` (thread-pool battery under the same 5s/2s budgets and the same severity table)
- Eight probes mirroring their Android counterparts: vpn, http_proxy, no_dns, redirect_loop, clock_skew, https_only, dns_hijack
- `gatepath/http_fetcher.py` (Date header, no-follow, 64 KiB cap) and `gatepath/diag_context.py` (the platform reads) live *outside* the package, which is what keeps it pure and testable
- `vpn_detector.detect_vpn_details()` exposes structured interfaces; `detect_vpn_interfaces()` is now a thin wrapper, so the audit-log label format is unchanged

Spec: `docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md` (PR 3 of 5). Plan, including six recorded design decisions: `docs/superpowers/plans/2026-07-19-desktop-diag-package.md`.

Deliberately absent versus Android, and why: `PrivateDnsBlocking`/`CellularFallback`/`SandboxedWebView` causes (Android-only concepts) and the `default_route_bypasses_captive` gate (desktop has no bound-vs-unbound socket distinction). PR 5's parity guard encodes that allowlist. `_is_private_or_loopback` mirrors Android's IPv4-only limitation on purpose — a silent behavioural divergence between mirrored engines would be worse than a shared documented gap.

## Test plan
- [ ] `python -m pytest desktop/ mockportal/` green
- [ ] `diag/` purity grep clean (no urllib/socket/os/dasbus/gi imports)
- [ ] CI desktop pytest + desktop e2e green
EOF
)"
```

Commit this plan doc on the branch alongside the work.
