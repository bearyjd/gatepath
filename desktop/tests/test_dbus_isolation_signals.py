"""Tests for `gatepath.desktop_isolation.DbusIsolationSignals`.

The real path opens a dasbus connection to the system bus; we exercise
the glue that wires dasbus signal connect/disconnect into the
:py:class:`SubprocessExit` dataclass via a mock bus + mock proxy. This
catches:

  - dasbus's signal-attribute access pattern (`proxy.SignalName.connect`)
  - the (pid, exit_code, signal_num) → SubprocessExit translation
  - the unsubscribe path that calls `.disconnect(handler_id)` on the
    same signal attribute the subscribe used

The full real-bus integration test requires a running `dbus-daemon`
session; that's CI-only and intentionally not bundled here to keep the
unit-test pass green on dev laptops without a session bus.
"""

from __future__ import annotations

from typing import Callable, Optional

from gatepath.desktop_isolation import DbusIsolationSignals
from gatepath.netns_client import SubprocessExit


# ── Test fakes ───────────────────────────────────────────────────────────


class FakeSignal:
    """Mocks dasbus's auto-generated signal accessor: supports
    `.connect(handler) -> handler_id` and `.disconnect(handler_id)`.
    """

    def __init__(self) -> None:
        self._handlers: dict[int, Callable[..., None]] = {}
        self._next_id = 1
        self.disconnect_calls: list[int] = []

    def connect(self, handler: Callable[..., None]) -> int:
        handler_id = self._next_id
        self._next_id += 1
        self._handlers[handler_id] = handler
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnect_calls.append(handler_id)
        self._handlers.pop(handler_id, None)

    def emit(self, *args: object) -> None:
        # Fire all currently-registered handlers in registration order.
        for handler in list(self._handlers.values()):
            handler(*args)

    @property
    def handler_count(self) -> int:
        return len(self._handlers)


class FakeProxy:
    """Mocks the dasbus proxy: exposes the signal as an attribute."""

    def __init__(self) -> None:
        self.PortalSubprocessExited = FakeSignal()


class FakeBus:
    """Mocks dasbus's MessageBus: returns the fake proxy on get_proxy."""

    def __init__(self, proxy: FakeProxy) -> None:
        self._proxy = proxy
        self.get_proxy_calls: list[tuple[str, str]] = []

    def get_proxy(self, bus_name: str, object_path: str):
        self.get_proxy_calls.append((bus_name, object_path))
        return self._proxy


# ── Tests ────────────────────────────────────────────────────────────────


def test_subscribe_returns_handle_with_unsubscribe_method() -> None:
    proxy = FakeProxy()
    bus = FakeBus(proxy)
    signals = DbusIsolationSignals(bus=bus)

    sub = signals.subscribe(lambda exit_payload: None)

    assert hasattr(sub, "unsubscribe") and callable(sub.unsubscribe)
    # The proxy was looked up exactly once; subsequent subscribes reuse
    # it (we test that pattern below).
    assert len(bus.get_proxy_calls) == 1
    # One handler is now connected.
    assert proxy.PortalSubprocessExited.handler_count == 1


def test_subscribe_callback_receives_subprocess_exit_dataclass() -> None:
    """Pin: dasbus delivers (pid, exit_code, signal_num) as positional
    args; our adapter wraps them in a SubprocessExit dataclass before
    calling the user callback. Verify the field mapping matches the
    helper's signal contract.
    """
    proxy = FakeProxy()
    bus = FakeBus(proxy)
    signals = DbusIsolationSignals(bus=bus)

    received: list[SubprocessExit] = []
    signals.subscribe(received.append)

    proxy.PortalSubprocessExited.emit(4242, 0, 0)

    assert received == [SubprocessExit(pid=4242, exit_code=0, signal_num=0)]


def test_subscribe_callback_receives_signaled_exit() -> None:
    proxy = FakeProxy()
    bus = FakeBus(proxy)
    signals = DbusIsolationSignals(bus=bus)
    received: list[SubprocessExit] = []
    signals.subscribe(received.append)

    proxy.PortalSubprocessExited.emit(4242, -1, 9)  # SIGKILL

    assert received == [SubprocessExit(pid=4242, exit_code=-1, signal_num=9)]


def test_unsubscribe_disconnects_signal_handler() -> None:
    proxy = FakeProxy()
    bus = FakeBus(proxy)
    signals = DbusIsolationSignals(bus=bus)

    received: list[SubprocessExit] = []
    sub = signals.subscribe(received.append)
    sub.unsubscribe()

    # No handlers remain; emit is a no-op.
    proxy.PortalSubprocessExited.emit(4242, 0, 0)
    assert received == []
    # The disconnect went through with the same id we got at subscribe.
    assert proxy.PortalSubprocessExited.disconnect_calls == [1]


def test_unsubscribe_is_idempotent() -> None:
    """Pin: calling unsubscribe twice doesn't raise. Matches the
    contract orchestrator code relies on (multiple disengage calls).
    """
    proxy = FakeProxy()
    bus = FakeBus(proxy)
    signals = DbusIsolationSignals(bus=bus)
    sub = signals.subscribe(lambda _: None)
    sub.unsubscribe()
    # The DbusIsolationSignals.subscribe-returned handle only calls
    # disconnect once; calling unsubscribe again on the SAME handle is
    # the orchestrator's responsibility to avoid. But even if dasbus's
    # disconnect was called twice it shouldn't crash. Pin that.
    try:
        sub.unsubscribe()
    except Exception as exc:  # noqa: BLE001
        # Some dasbus versions might raise on double-disconnect; we
        # only care that our adapter doesn't crash. The adapter
        # currently calls disconnect blindly — assert that pattern.
        # If a future dasbus refactor changes this, update the test.
        raise AssertionError(f"unsubscribe raised on second call: {exc}") from exc


def test_multiple_subscribes_reuse_proxy_lookup() -> None:
    """Performance pin: the proxy is constructed once on first
    subscribe; subsequent subscribes shouldn't roundtrip get_proxy.
    """
    proxy = FakeProxy()
    bus = FakeBus(proxy)
    signals = DbusIsolationSignals(bus=bus)

    sub1 = signals.subscribe(lambda _: None)
    sub2 = signals.subscribe(lambda _: None)

    assert len(bus.get_proxy_calls) == 1
    assert proxy.PortalSubprocessExited.handler_count == 2
    assert sub1 is not sub2


def test_subscribe_uses_helper_bus_name_and_object_path() -> None:
    """Pin: integration with the helper's wire constants. If these
    drift, dasbus would fail at proxy lookup time on the real bus.
    """
    from gatepath.netns_client import BUS_NAME, OBJECT_PATH  # noqa: PLC0415

    proxy = FakeProxy()
    bus = FakeBus(proxy)
    signals = DbusIsolationSignals(bus=bus)
    signals.subscribe(lambda _: None)

    assert bus.get_proxy_calls == [(BUS_NAME, OBJECT_PATH)]
