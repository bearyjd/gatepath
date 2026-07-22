"""Unit tests for DbusNMConnectivitySignals subscribe/adapter/unsubscribe wiring.

Fully headless: no dasbus, no gi, no real bus. A fake bus returns a fake proxy
whose ``StateChanged`` exposes ``connect``/``disconnect`` (like a dasbus
auto-generated signal helper). Injecting the fake bus proves the wiring without
importing dasbus at all — the same posture as DbusIsolationSignals, which is
otherwise only exercised by the integration harness.
"""

from __future__ import annotations

from typing import Callable, List

from gatepath.portal_monitor import (
    NM_BUS_NAME,
    NM_OBJECT_PATH,
    DbusNMConnectivitySignals,
)


class FakeStateChangedSignal:
    """Mimics a dasbus signal helper: connect(handler) -> id; disconnect(id)."""

    def __init__(self) -> None:
        self.handlers: dict[int, Callable[[int], None]] = {}
        self.disconnected: List[int] = []
        self._next_id = 1

    def connect(self, handler: Callable[[int], None]) -> int:
        handler_id = self._next_id
        self._next_id += 1
        self.handlers[handler_id] = handler
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnected.append(handler_id)

    def fire(self, state: int) -> None:
        """Invoke every connected handler with an NMState arg (mimics NM)."""
        for handler in self.handlers.values():
            handler(state)


class FakeProxy:
    def __init__(self) -> None:
        self.StateChanged = FakeStateChangedSignal()


class FakeBus:
    """Records get_proxy calls and returns a single shared fake proxy."""

    def __init__(self) -> None:
        self.proxy = FakeProxy()
        self.get_proxy_calls: List[tuple[str, str]] = []

    def get_proxy(self, bus_name: str, object_path: str) -> FakeProxy:
        self.get_proxy_calls.append((bus_name, object_path))
        return self.proxy


def test_subscribe_builds_proxy_once_and_reuses_it() -> None:
    bus = FakeBus()
    signals = DbusNMConnectivitySignals(bus=bus)

    signals.subscribe(lambda: None)
    signals.subscribe(lambda: None)

    # Proxy built exactly once, against the NM manager object.
    assert bus.get_proxy_calls == [(NM_BUS_NAME, NM_OBJECT_PATH)]
    assert len(bus.proxy.StateChanged.handlers) == 2


def test_adapter_drops_state_arg_and_calls_zero_arg_callback() -> None:
    bus = FakeBus()
    signals = DbusNMConnectivitySignals(bus=bus)
    calls: List[tuple] = []

    signals.subscribe(lambda: calls.append(()))

    # Fire the signal with an NMState int; the adapter must drop it and call
    # the zero-arg callback.
    bus.proxy.StateChanged.fire(70)
    assert calls == [()]


def test_unsubscribe_disconnects_recorded_handler() -> None:
    bus = FakeBus()
    signals = DbusNMConnectivitySignals(bus=bus)

    subscription = signals.subscribe(lambda: None)
    (handler_id,) = bus.proxy.StateChanged.handlers.keys()

    subscription.unsubscribe()
    assert bus.proxy.StateChanged.disconnected == [handler_id]


def test_unsubscribe_swallows_disconnect_errors() -> None:
    bus = FakeBus()

    def boom(_handler_id: int) -> None:
        raise RuntimeError("disconnect boom")

    signals = DbusNMConnectivitySignals(bus=bus)
    subscription = signals.subscribe(lambda: None)
    bus.proxy.StateChanged.disconnect = boom  # type: ignore[assignment]

    # Must not raise (mirrors DbusIsolationSignals' logged-and-swallowed path).
    subscription.unsubscribe()


def test_injected_bus_does_not_import_dasbus() -> None:
    import sys

    dasbus_before = sys.modules.get("dasbus")
    bus = FakeBus()
    signals = DbusNMConnectivitySignals(bus=bus)
    signals.subscribe(lambda: None)

    # Injecting a bus must not have imported dasbus (headless-safe).
    assert sys.modules.get("dasbus") is dasbus_before
