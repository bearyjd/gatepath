"""Captive-portal isolation orchestrator (Phase 5c.2).

Wires :py:class:`gatepath.netns_client.NetnsClient` into a single lifecycle:

    engage(portal_url, interface)  →
        helper.SetupCaptive(interface)  →
        helper.LaunchPortal(portal_url)
    wait_for_subprocess()  →
        block until PortalSubprocessExited signal arrives
    disengage()  →
        helper.TeardownCaptive()

The orchestrator does NOT spawn anything itself — the helper owns that
because `setns(2)` to a root-owned netns requires CAP_SYS_ADMIN. See
`.claude/PRPs/plans/desktop-netns-spawn.plan.md` for the architectural
rationale.

Top-level imports stay stdlib + typing only (matches portal_monitor's
pattern). The dasbus signal subscription is imported lazily inside the
:py:class:`DbusIsolationSignals` constructor so tests don't need a bus.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Optional, Protocol, Union, runtime_checkable

from gatepath.netns_client import (
    LaunchPortalRefused,
    LaunchPortalSuccess,
    NetnsClient,
    RefusalReason,
    SetupRefused,
    SetupSuccess,
    SubprocessExit,
    TeardownRefused,
    TeardownSuccess,
)

logger = logging.getLogger(__name__)

# Default ceiling for `wait_for_subprocess`. Matches
# `portal_session.SESSION_TIMEOUT_SECONDS` (the existing 10-minute session
# cap) so a stuck subprocess can't outlive a normal session.
DEFAULT_WAIT_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class EngageSuccess:
    """Both setup and launch succeeded; subprocess is running in the netns."""

    netns_path: str
    pid: int


@dataclass(frozen=True)
class EngageRefused:
    """Either step refused. ``stage`` records which step failed.

    ``stage`` is one of:
      - ``"setup"`` — `SetupCaptive` failed; no netns was created.
      - ``"launch"`` — `SetupCaptive` succeeded but `LaunchPortal` failed.
        Caller MUST call :py:meth:`DesktopIsolation.disengage` to tear the
        netns back down. The orchestrator does not auto-tear-down here so
        the caller can record the partial state in audit before cleanup.
    """

    reason: RefusalReason
    detail: str
    stage: str


EngageResult = Union[EngageSuccess, EngageRefused]


@dataclass(frozen=True)
class WaitTimeout:
    """`wait_for_subprocess` returned because the timeout elapsed."""


@dataclass(frozen=True)
class WaitInterrupted:
    """`wait_for_subprocess` was interrupted via :py:meth:`DesktopIsolation.cancel_wait`."""


WaitResult = Union[SubprocessExit, WaitTimeout, WaitInterrupted]


def isolation_should_engage(
    isolation: "Optional[DesktopIsolation]",
    lookup: "Optional[CaptiveInterfaceLookup]",
) -> bool:
    """Return True iff the caller should attempt the isolated path.

    Both inputs must be non-None AND the lookup must currently report a
    captive interface. Pure-logic gate exposed for unit tests; the GTK
    window duplicates the check inline so it can capture the interface
    name returned by the lookup.
    """
    if isolation is None or lookup is None:
        return False
    return lookup.get_captive_interface() is not None


# Forward-only type imports for the helper above. Done at the bottom of
# the module so the rest of the file (which lives at the top of the
# import graph) doesn't pull portal_monitor in eagerly.
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gatepath.portal_monitor import CaptiveInterfaceLookup


def wait_result_to_close_reason(result: WaitResult):
    """Map a :py:class:`WaitResult` to a :py:class:`CloseReason`.

    Pure function so callers can use it without depending on the rest of
    the orchestrator. The mapping:

    - ``SubprocessExit`` with ``is_clean`` → ``PORTAL_COMPLETED``
    - ``SubprocessExit`` non-clean → ``ERROR``
    - ``WaitTimeout`` → ``TIMEOUT``
    - ``WaitInterrupted`` → ``USER_DISMISSED``
    """
    # Lazy import to avoid pulling portal_session in at module import.
    from gatepath.portal_session import CloseReason  # noqa: PLC0415

    if isinstance(result, SubprocessExit):
        if result.is_clean:
            return CloseReason.PORTAL_COMPLETED
        return CloseReason.ERROR
    if isinstance(result, WaitTimeout):
        return CloseReason.TIMEOUT
    if isinstance(result, WaitInterrupted):
        return CloseReason.USER_DISMISSED
    raise TypeError(f"unknown WaitResult: {result!r}")


@dataclass(frozen=True)
class DisengageSuccess:
    """Helper torn the active session down."""


@dataclass(frozen=True)
class DisengageRefused:
    """Teardown refused. ``NOT_ACTIVE`` is benign; treat as already-disengaged."""

    reason: RefusalReason
    detail: str


DisengageResult = Union[DisengageSuccess, DisengageRefused]


@runtime_checkable
class IsolationSignals(Protocol):
    """Surface for subscribing to the helper's `PortalSubprocessExited` signal.

    Real impl is :py:class:`DbusIsolationSignals` (uses dasbus); tests
    inject :py:class:`FakeIsolationSignals`. Abstracted so the orchestrator
    is testable without a bus.
    """

    def subscribe(self, callback: "ExitCallback") -> "Subscription":
        """Register a callback for subprocess-exit events.

        Returns a :py:class:`Subscription` whose :py:meth:`unsubscribe`
        method MUST be called to release the underlying handler. The
        orchestrator's :py:meth:`DesktopIsolation.engage` registers a
        subscription and disposes it in :py:meth:`disengage`.
        """
        ...


ExitCallback = "callable that takes a SubprocessExit"


class Subscription(Protocol):
    """Handle returned by :py:meth:`IsolationSignals.subscribe`.

    Calling :py:meth:`unsubscribe` releases the underlying signal handler.
    Idempotent — multiple unsubscribes are safe.
    """

    def unsubscribe(self) -> None:
        ...


class DesktopIsolation:
    """Orchestrates the engage → wait → disengage lifecycle.

    Holds a :py:class:`NetnsClient` for D-Bus method calls and an
    :py:class:`IsolationSignals` for signal subscription. Construction is
    cheap; the bus connection happens inside ``client.connect()`` and
    ``signals``'s implementation.
    """

    def __init__(self, client: NetnsClient, signals: IsolationSignals) -> None:
        self._client = client
        self._signals = signals
        self._subscription: Optional[Subscription] = None
        self._exit_event = threading.Event()
        self._cancel_event = threading.Event()
        self._observed_exit: Optional[SubprocessExit] = None
        self._observed_lock = threading.Lock()

    def engage(
        self,
        portal_url: str,
        interface_name: str,
        *,
        wayland_display: str = "",
        x_display: str = "",
        x_authority: str = "",
    ) -> EngageResult:
        """Set up the netns AND launch the portal subprocess.

        Two-step operation: helper.SetupCaptive then helper.LaunchPortal.
        Either step can refuse; the result records which stage failed
        via :py:attr:`EngageRefused.stage`.

        On launch failure after setup succeeded, the netns is left in
        place — caller MUST call :py:meth:`disengage` to tear it down.

        The three display values (``""`` = unset) are the graphical-session
        identifiers the WebView needs to render; they are read from the UI
        process environment at the call boundary and forwarded to the helper
        (DESK-004). This orchestrator stays pure — it does not read
        ``os.environ`` itself.
        """
        self._exit_event.clear()
        self._cancel_event.clear()
        with self._observed_lock:
            self._observed_exit = None

        # Subscribe BEFORE the launch so we don't miss a fast-failing
        # subprocess that exits before subscription completes.
        if self._subscription is None:
            self._subscription = self._signals.subscribe(self._on_exit)

        setup_result = self._client.setup_captive(interface_name)
        if isinstance(setup_result, SetupRefused):
            self._cleanup_subscription()
            return EngageRefused(
                reason=setup_result.reason,
                detail=setup_result.detail,
                stage="setup",
            )
        assert isinstance(setup_result, SetupSuccess)

        launch_result = self._client.launch_portal(
            portal_url,
            wayland_display=wayland_display,
            x_display=x_display,
            x_authority=x_authority,
        )
        if isinstance(launch_result, LaunchPortalRefused):
            return EngageRefused(
                reason=launch_result.reason,
                detail=launch_result.detail,
                stage="launch",
            )
        assert isinstance(launch_result, LaunchPortalSuccess)

        return EngageSuccess(netns_path=setup_result.netns_path, pid=launch_result.pid)

    def wait_for_subprocess(
        self, timeout_seconds: float = DEFAULT_WAIT_TIMEOUT_SECONDS
    ) -> WaitResult:
        """Block until the subprocess exits, ``cancel_wait`` is called, or timeout.

        Returns:
          - :py:class:`SubprocessExit` — helper emitted PortalSubprocessExited.
          - :py:class:`WaitInterrupted` — caller invoked ``cancel_wait``.
          - :py:class:`WaitTimeout` — neither happened within the timeout.

        The wait can be called only once per engage cycle; subsequent calls
        before a fresh ``engage`` return :py:class:`WaitTimeout` immediately
        because the events are already set/clear in a steady state.
        """
        deadline = timeout_seconds
        # Wait on the union of "exit happened" and "cancel requested".
        # threading.Event doesn't support multi-wait directly; loop with
        # short slices and check both. 100ms slice is below human-perceptible
        # latency for the cancel path.
        slice_seconds = 0.1
        elapsed = 0.0
        while elapsed < deadline:
            if self._exit_event.wait(slice_seconds):
                with self._observed_lock:
                    exit_payload = self._observed_exit
                if exit_payload is not None:
                    return exit_payload
                # Event set but no payload? Shouldn't happen; treat as
                # timeout so caller doesn't loop forever on bad state.
                logger.error("exit_event set without observed_exit payload")
                return WaitTimeout()
            if self._cancel_event.is_set():
                return WaitInterrupted()
            elapsed += slice_seconds
        return WaitTimeout()

    def cancel_wait(self) -> None:
        """Wake up a blocked :py:meth:`wait_for_subprocess` with WaitInterrupted.

        Used when the parent UI is shutting down and wants to abandon the
        wait without waiting out the full timeout. Idempotent.
        """
        self._cancel_event.set()

    def disengage(self) -> DisengageResult:
        """Tear down the active session.

        Always attempts the teardown — even if no engage succeeded — so
        callers can use this as a "bring helper to clean state" call.
        ``NOT_ACTIVE`` is treated as a benign refusal.
        """
        self._cleanup_subscription()
        result = self._client.teardown_captive()
        if isinstance(result, TeardownRefused):
            return DisengageRefused(reason=result.reason, detail=result.detail)
        assert isinstance(result, TeardownSuccess)
        return DisengageSuccess()

    def _on_exit(self, exit_payload: SubprocessExit) -> None:
        """Signal-callback. Stores payload + sets the exit event."""
        with self._observed_lock:
            self._observed_exit = exit_payload
        self._exit_event.set()

    def _cleanup_subscription(self) -> None:
        if self._subscription is not None:
            self._subscription.unsubscribe()
            self._subscription = None


# ── Production IsolationSignals impl (lazy dasbus import) ────────────────


class DbusIsolationSignals:
    """System-bus subscription to ``PortalSubprocessExited``.

    Lazy dasbus import (matches `netns_client.NetnsClient.connect`'s pattern):
    constructing :py:class:`DesktopIsolation` with this signals impl raises
    if dasbus or the bus is unavailable.
    """

    def __init__(self, bus=None) -> None:
        """Construct a signals subscriber.

        ``bus`` is an optional dasbus message-bus instance; ``None`` (the
        default) uses the system bus. Integration tests inject a session
        bus to exercise the signal-subscription path without root.
        """
        if bus is None:
            try:
                from dasbus.connection import SystemMessageBus  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(f"dasbus not available: {exc}") from exc
            bus = SystemMessageBus()
        self._bus = bus
        self._proxy = None  # constructed on first subscribe

    def subscribe(self, callback) -> Subscription:  # type: ignore[no-untyped-def]
        from gatepath.netns_client import BUS_NAME, OBJECT_PATH  # noqa: PLC0415

        if self._proxy is None:
            self._proxy = self._bus.get_proxy(BUS_NAME, OBJECT_PATH)

        def adapter(pid: int, exit_code: int, signal_num: int) -> None:
            callback(SubprocessExit(pid=pid, exit_code=exit_code, signal_num=signal_num))

        # dasbus signals: the proxy exposes the signal as an attribute that
        # supports `.connect(handler)` returning a handler ID; disconnect
        # via `.disconnect(id)`. The exact method names are dasbus's
        # auto-generated signal helpers.
        signal = self._proxy.PortalSubprocessExited
        handler_id = signal.connect(adapter)

        proxy = self._proxy

        class _DbusSubscription:
            def unsubscribe(self) -> None:
                try:
                    proxy.PortalSubprocessExited.disconnect(handler_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("signal disconnect raised: %s", exc)

        return _DbusSubscription()
