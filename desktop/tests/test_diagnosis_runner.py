"""Unit tests for the off-main-loop diagnostics runner seam.

All three collaborators (``dispatch``, ``engine_factory``,
``context_builder``) are injectable, so these tests exercise the real worker
thread with no GTK and no network. The worker is a genuine daemon thread, so
each test awaits its completion deterministically via a ``threading.Event``
rather than sleeping.
"""

from __future__ import annotations

import threading
from typing import Callable, List, Tuple

from gatepath import diagnosis_runner
from gatepath.diag.engine import DiagnosisResult
from gatepath.diag.report import (
    Cause,
    Healthy,
    Inconclusive,
    NO_ACTION,
)


class _RecordingDispatch:
    """A stand-in for ``GLib.idle_add`` that records the (callback, arg) call.

    Signals a ``threading.Event`` when invoked so a test can await the worker
    thread's hand-off without sleeping.
    """

    def __init__(self) -> None:
        self.calls: List[Tuple[Callable[[object], object], object]] = []
        self.called = threading.Event()

    def __call__(self, callback: Callable[[object], object], arg: object) -> bool:
        self.calls.append((callback, arg))
        self.called.set()
        return False


def _healthy_result() -> DiagnosisResult:
    return DiagnosisResult(top=Healthy(), checks=(), recommended=NO_ACTION)


class _FakeEngine:
    def __init__(self, result: DiagnosisResult) -> None:
        self._result = result
        self.received_ctx: object = None

    def run(self, ctx: object) -> DiagnosisResult:
        self.received_ctx = ctx
        return self._result


def test_success_path_delivers_result_through_dispatch() -> None:
    result = _healthy_result()
    engine = _FakeEngine(result)
    sentinel_ctx = object()
    seen_interface: List[str] = []

    def context_builder(interface_name: str) -> object:
        seen_interface.append(interface_name)
        return sentinel_ctx

    dispatch = _RecordingDispatch()
    delivered: List[DiagnosisResult] = []

    diagnosis_runner.run_diagnostics_async(
        "wlan0",
        delivered.append,
        engine_factory=lambda: engine,
        context_builder=context_builder,
        dispatch=dispatch,
    )

    assert dispatch.called.wait(timeout=5.0), "worker never dispatched a result"

    # Exactly one dispatch, carrying the continuation and the engine's result.
    assert len(dispatch.calls) == 1
    callback, arg = dispatch.calls[0]
    assert arg is result

    # The engine saw the context the builder produced for our interface.
    assert seen_interface == ["wlan0"]
    assert engine.received_ctx is sentinel_ctx

    # Driving the continuation hands the exact result to on_result.
    callback(arg)
    assert delivered == [result]


def test_engine_exception_becomes_inconclusive() -> None:
    def failing_factory() -> _FakeEngine:
        raise RuntimeError("engine boom")

    dispatch = _RecordingDispatch()

    diagnosis_runner.run_diagnostics_async(
        "wlan0",
        lambda _r: None,
        engine_factory=failing_factory,
        context_builder=lambda name: object(),
        dispatch=dispatch,
    )

    assert dispatch.called.wait(timeout=5.0), "worker never dispatched a result"
    assert len(dispatch.calls) == 1
    _callback, arg = dispatch.calls[0]

    assert isinstance(arg, DiagnosisResult)
    assert arg.top.cause is Cause.INCONCLUSIVE
    assert isinstance(arg.top, Inconclusive)
    assert arg.checks == ()
    assert arg.recommended == NO_ACTION
    assert any("engine boom" in err for err in arg.top.probe_errors)


def test_context_builder_exception_becomes_inconclusive() -> None:
    def failing_builder(interface_name: str) -> object:
        raise ValueError("context boom")

    dispatch = _RecordingDispatch()

    diagnosis_runner.run_diagnostics_async(
        "eth0",
        lambda _r: None,
        engine_factory=lambda: _FakeEngine(_healthy_result()),
        context_builder=failing_builder,
        dispatch=dispatch,
    )

    assert dispatch.called.wait(timeout=5.0), "worker never dispatched a result"
    _callback, arg = dispatch.calls[0]

    assert isinstance(arg, DiagnosisResult)
    assert arg.top.cause is Cause.INCONCLUSIVE
    assert isinstance(arg.top, Inconclusive)
    assert any("context boom" in err for err in arg.top.probe_errors)


def test_interface_resolver_runs_on_the_worker_and_feeds_the_context() -> None:
    """When an ``interface_resolver`` is given it is called on the worker
    thread (not the caller's) and its return value is what the context builder
    receives — this is how the blocking D-Bus lookup is kept off the main loop.
    """
    engine = _FakeEngine(_healthy_result())
    seen_interface: List[str] = []
    resolver_thread: List[str] = []

    def resolver() -> str:
        resolver_thread.append(threading.current_thread().name)
        return "resolved-wlan1"

    def context_builder(interface_name: str) -> object:
        seen_interface.append(interface_name)
        return object()

    dispatch = _RecordingDispatch()

    diagnosis_runner.run_diagnostics_async(
        None,
        lambda _r: None,
        interface_resolver=resolver,
        engine_factory=lambda: engine,
        context_builder=context_builder,
        dispatch=dispatch,
    )

    assert dispatch.called.wait(timeout=5.0), "worker never dispatched a result"
    # The resolver's value reached the context builder...
    assert seen_interface == ["resolved-wlan1"]
    # ...and it ran on the runner's own daemon worker, not the test thread.
    assert resolver_thread == [diagnosis_runner._THREAD_NAME]


def test_interface_resolver_failure_becomes_inconclusive() -> None:
    """A resolver that raises (e.g. NM unreachable) degrades like any other
    worker failure rather than killing the thread or dropping the result.
    """

    def failing_resolver() -> str:
        raise OSError("dbus down")

    dispatch = _RecordingDispatch()

    diagnosis_runner.run_diagnostics_async(
        None,
        lambda _r: None,
        interface_resolver=failing_resolver,
        engine_factory=lambda: _FakeEngine(_healthy_result()),
        context_builder=lambda name: object(),
        dispatch=dispatch,
    )

    assert dispatch.called.wait(timeout=5.0), "worker never dispatched a result"
    _callback, arg = dispatch.calls[0]
    assert isinstance(arg, DiagnosisResult)
    assert arg.top.cause is Cause.INCONCLUSIVE
    assert any("dbus down" in err for err in arg.top.probe_errors)


def test_dispatch_receives_the_on_result_continuation() -> None:
    result = _healthy_result()
    dispatch = _RecordingDispatch()
    received: List[DiagnosisResult] = []

    def on_result(r: DiagnosisResult) -> None:
        received.append(r)

    diagnosis_runner.run_diagnostics_async(
        "wlan0",
        on_result,
        engine_factory=lambda: _FakeEngine(result),
        context_builder=lambda name: object(),
        dispatch=dispatch,
    )

    assert dispatch.called.wait(timeout=5.0)
    callback, arg = dispatch.calls[0]

    # The dispatched callback, when run on the (fake) main loop, invokes
    # on_result with the delivered result and returns a falsy value so
    # GLib.idle_add would not repeat it.
    repeat = callback(arg)
    assert not repeat
    assert received == [result]
