"""Unit tests for NMSignalMonitor — event-driven captive-portal detection.

Fully headless: no dasbus, no gi, no network. A fake NMConnectivitySignals
records the subscribed callback and lets the test fire it; a fake probe_fn
returns a portal URL (or None), and worker-thread completion is awaited
deterministically via threading.Events — never sleeps.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Optional

from gatepath.portal_monitor import (
    Monitor,
    NMConnectivitySignals,
    NMSignalMonitor,
    Subscription,
    start_nm_monitor,
)


class FakeSubscription:
    """Records unsubscribe() and detaches itself from the fake signals."""

    def __init__(self, signals: "FakeNMConnectivitySignals") -> None:
        self._signals = signals
        self.unsubscribed = False

    def unsubscribe(self) -> None:
        self.unsubscribed = True
        self._signals._active_callback = None


class FakeNMConnectivitySignals(NMConnectivitySignals):
    """Records the subscribed callback and lets tests fire it manually."""

    def __init__(self) -> None:
        self._active_callback: Optional[Callable[[], None]] = None
        self.subscribe_count = 0
        self.last_subscription: Optional[FakeSubscription] = None

    def subscribe(self, callback: Callable[[], None]) -> Subscription:
        self._active_callback = callback
        self.subscribe_count += 1
        self.last_subscription = FakeSubscription(self)
        return self.last_subscription

    def fire(self) -> None:
        """Invoke the subscribed callback synchronously (mimics a signal)."""
        if self._active_callback is not None:
            self._active_callback()


class BlockingProbe:
    """A probe_fn that blocks inside the call until its gate is released.

    Lets a test observe a probe *in flight* and assert on debounce without
    sleeps. Records how many times it was entered.
    """

    def __init__(self, url: Optional[str]) -> None:
        self._url = url
        self.entered = threading.Event()
        self.gate = threading.Event()
        self.calls = 0
        self._lock = threading.Lock()

    def __call__(self) -> Optional[str]:
        with self._lock:
            self.calls += 1
        self.entered.set()
        self.gate.wait()
        return self._url


def _join_worker(monitor: NMSignalMonitor) -> None:
    """Deterministically wait for the most recent worker thread to finish."""
    worker = monitor._worker  # noqa: SLF001 — test owns the fake's internals
    if worker is not None:
        worker.join(timeout=5)
        assert not worker.is_alive()


def test_signal_fires_and_probe_returns_url_detects_once() -> None:
    signals = FakeNMConnectivitySignals()
    detected: List[str] = []

    monitor = NMSignalMonitor(
        signals=signals,
        probe_fn=lambda: "http://portal.example/login",
        on_portal_detected=detected.append,
    )
    monitor.start()
    signals.fire()
    _join_worker(monitor)

    assert detected == ["http://portal.example/login"]


def test_signal_fires_and_probe_returns_none_does_not_detect() -> None:
    signals = FakeNMConnectivitySignals()
    detected: List[str] = []

    monitor = NMSignalMonitor(
        signals=signals,
        probe_fn=lambda: None,
        on_portal_detected=detected.append,
    )
    monitor.start()
    signals.fire()
    _join_worker(monitor)

    assert detected == []


def test_debounce_coalesces_rapid_signals_into_one_probe() -> None:
    signals = FakeNMConnectivitySignals()
    detected: List[str] = []
    probe = BlockingProbe("http://portal.example/login")

    monitor = NMSignalMonitor(
        signals=signals,
        probe_fn=probe,
        on_portal_detected=detected.append,
    )
    monitor.start()

    # First signal spawns a worker that enters the probe and blocks.
    signals.fire()
    assert probe.entered.wait(timeout=5)
    first_worker = monitor._worker  # noqa: SLF001

    # Second signal while the probe is in flight must be debounced — no new
    # worker, no second probe entry.
    signals.fire()
    assert monitor._worker is first_worker  # noqa: SLF001
    assert probe.calls == 1

    # Release the in-flight probe; exactly one detection results.
    probe.gate.set()
    _join_worker(monitor)
    assert detected == ["http://portal.example/login"]

    # A later signal (after completion) probes again.
    probe.gate.set()  # keep the gate open so the next probe returns promptly
    signals.fire()
    _join_worker(monitor)
    assert probe.calls == 2
    assert detected == [
        "http://portal.example/login",
        "http://portal.example/login",
    ]


def test_probe_exception_clears_in_flight_and_does_not_detect() -> None:
    signals = FakeNMConnectivitySignals()
    detected: List[str] = []
    calls: List[int] = []

    def raising_probe() -> Optional[str]:
        calls.append(1)
        raise RuntimeError("probe boom")

    monitor = NMSignalMonitor(
        signals=signals,
        probe_fn=raising_probe,
        on_portal_detected=detected.append,
    )
    monitor.start()

    # First signal: probe raises, worker swallows it, in-flight is cleared.
    signals.fire()
    _join_worker(monitor)
    assert detected == []
    assert len(calls) == 1

    # A subsequent signal still probes (flag was cleared in the finally).
    signals.fire()
    _join_worker(monitor)
    assert detected == []
    assert len(calls) == 2


def test_stop_unsubscribes() -> None:
    signals = FakeNMConnectivitySignals()
    monitor = NMSignalMonitor(
        signals=signals,
        probe_fn=lambda: None,
        on_portal_detected=lambda _url: None,
    )
    monitor.start()
    subscription = signals.last_subscription
    assert subscription is not None
    assert not subscription.unsubscribed

    monitor.stop()
    assert subscription.unsubscribed
    # Idempotent: a second stop is safe.
    monitor.stop()


def test_start_twice_does_not_double_subscribe() -> None:
    signals = FakeNMConnectivitySignals()
    monitor = NMSignalMonitor(
        signals=signals,
        probe_fn=lambda: None,
        on_portal_detected=lambda _url: None,
    )
    monitor.start()
    monitor.start()
    assert signals.subscribe_count == 1


# ── start_nm_monitor path selection ─────────────────────────────────────


def test_start_nm_monitor_uses_signal_path_when_factory_succeeds() -> None:
    signals = FakeNMConnectivitySignals()

    monitor = start_nm_monitor(
        lambda _url: None,
        signals_factory=lambda: signals,
    )

    try:
        assert isinstance(monitor, NMSignalMonitor)
        # Started: the fake recorded exactly one subscription.
        assert signals.subscribe_count == 1
    finally:
        monitor.stop()


def test_start_nm_monitor_falls_back_to_polling_when_factory_raises() -> None:
    def raising_factory() -> NMConnectivitySignals:
        raise RuntimeError("no dasbus / no bus / no NM")

    monitor = start_nm_monitor(
        lambda _url: None,
        signals_factory=raising_factory,
    )

    try:
        assert isinstance(monitor, Monitor)
        # Started: the polling thread is alive.
        assert monitor._thread is not None  # noqa: SLF001
        assert monitor._thread.is_alive()  # noqa: SLF001
    finally:
        monitor.stop()


def test_start_nm_monitor_public_two_arg_form_still_works() -> None:
    signals = FakeNMConnectivitySignals()

    # 2-arg positional/kw form (as app.py calls it) plus the injected factory.
    monitor = start_nm_monitor(
        lambda _url: None,
        probe_url="http://probe.example/generate_204",
        signals_factory=lambda: signals,
    )

    try:
        assert isinstance(monitor, NMSignalMonitor)
        assert signals.subscribe_count == 1
    finally:
        monitor.stop()
