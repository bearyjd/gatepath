"""Runs the diagnostic battery off the GTK main loop, then delivers the
result back onto it.

This module lives *outside* ``gatepath.diag`` on purpose: it performs I/O
(via the injected ``context_builder``/``engine_factory``, which reach real
sockets and files) and it touches GLib. That mirrors ``diag_context``'s rule
— the pure ``diag/`` package stays free of both, and the code that wires it
to the real world lives here.

The only GTK dependency is the dispatcher, which defaults to
``GLib.idle_add`` and is imported lazily *inside* the function so that this
module imports without PyGObject. Injecting the dispatcher (alongside
``engine_factory`` and ``context_builder``) lets the whole seam be unit
tested with no GTK and no network.
"""

from __future__ import annotations

import threading
from typing import Callable

from gatepath.diag.engine import DiagnosisResult
from gatepath.diag.report import Inconclusive, NO_ACTION
from gatepath.diag_context import build_probe_context, default_engine

# A callable that schedules ``callback(arg)`` to run on the GTK main loop.
# ``GLib.idle_add`` has exactly this shape.
Dispatch = Callable[[Callable[[DiagnosisResult], object], DiagnosisResult], object]

_THREAD_NAME = "gatepath-diagnostics"


def _inconclusive_result(exc: BaseException) -> DiagnosisResult:
    """Turn a worker-side failure into a benign, renderable result.

    A context-build or engine failure must never kill the worker thread or
    silently drop the user's request — it becomes an ``Inconclusive`` top
    finding carrying the error text, with no checks and no recommended
    action, delivered through the same dispatch path as a real result.
    """
    return DiagnosisResult(
        top=Inconclusive(probe_errors=(f"diagnostics failed: {exc}",)),
        checks=(),
        recommended=NO_ACTION,
    )


def run_diagnostics_async(
    interface_name: str,
    on_result: Callable[[DiagnosisResult], None],
    *,
    engine_factory: Callable[[], object] = default_engine,
    context_builder: Callable[[str], object] = build_probe_context,
    dispatch: Dispatch | None = None,
) -> None:
    """Run the diagnostic battery for *interface_name* on a daemon thread and
    deliver its ``DiagnosisResult`` to *on_result* on the GTK main loop.

    The worker builds a probe context (``context_builder``), runs the engine
    (``engine_factory().run(ctx)``), and hands the result back via *dispatch*
    — which defaults to ``GLib.idle_add`` (imported lazily so this module
    stays importable without PyGObject). Any exception raised while building
    the context or running the engine is converted to an ``Inconclusive``
    result and delivered through the same path; the worker never raises.
    """
    if dispatch is None:
        from gi.repository import GLib  # Lazy: keeps the module gi-free to import.

        dispatch = GLib.idle_add

    def _deliver(result: DiagnosisResult) -> bool:
        """GTK-thread continuation. Returns False so idle_add won't repeat."""
        on_result(result)
        return False

    def _worker() -> None:
        try:
            ctx = context_builder(interface_name)
            result = engine_factory().run(ctx)
        except Exception as exc:  # noqa: BLE001 — any failure becomes Inconclusive.
            # Deliberately Exception, not BaseException: KeyboardInterrupt /
            # SystemExit should tear the process down, not be folded into a
            # diagnostic finding (matching DiagnosticEngine.run).
            result = _inconclusive_result(exc)
        dispatch(_deliver, result)

    threading.Thread(target=_worker, name=_THREAD_NAME, daemon=True).start()
