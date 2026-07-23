"""Portal monitor — detects captive portals via NetworkManager (dasbus) or polling.

Top-level imports: only stdlib + typing.
GTK / dasbus imports happen INSIDE the start_nm_monitor() function.

For environments without dasbus (or in tests), use the pure-Python
polling Monitor class which accepts an injectable probe_fn callable.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# NM_CONNECTIVITY_PORTAL — matches the helper's `NM_CONNECTIVITY_PORTAL = 2`.
# A device whose `Ip4Connectivity` property returns this value is currently
# behind a captive portal, per NetworkManager's own classification. NB: the
# Device interface has no bare `Connectivity` property — it was split into
# `Ip4Connectivity` / `Ip6Connectivity` in NetworkManager 1.16 (see
# gatepath-netns-helper's network_manager.rs module docs for the same note
# on the Rust side; this Python client must stay in lockstep with it).
NM_CONNECTIVITY_PORTAL = 2

# DEVICE_TYPE_WIFI — matches the helper's interface validator (which
# accepts only WiFi-prefix names). NetworkManager's NMDeviceType enum:
# 2 = WiFi.
NM_DEVICE_TYPE_WIFI = 2

# Polling interval when using the fallback poller (seconds).
_DEFAULT_POLL_INTERVAL = 30.0


class Monitor:
    """Polling-based captive-portal monitor.

    Calls probe_fn() on a daemon thread every poll_interval seconds.
    When probe_fn returns a portal URL, on_portal_detected is called
    on the same background thread.

    Designed to be fully unit-testable: inject fake probe_fn and
    on_portal_detected callbacks.
    """

    def __init__(
        self,
        probe_fn: Callable[[], Optional[str]],
        on_portal_detected: Callable[[str], None],
        poll_interval: float = _DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._probe_fn = probe_fn
        self._on_portal_detected = on_portal_detected
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background polling thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="gatepath-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("Portal monitor started (poll interval %.1fs)", self._poll_interval)

    def stop(self) -> None:
        """Signal the background thread to stop and wait for it."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 2)
        logger.info("Portal monitor stopped")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                portal_url = self._probe_fn()
                if portal_url is not None:
                    logger.info("Portal detected via probe: %s", portal_url)
                    self._on_portal_detected(portal_url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Monitor probe error: %s", exc)
            self._stop_event.wait(self._poll_interval)


class Subscription(Protocol):
    """Handle returned by :py:meth:`NMConnectivitySignals.subscribe`.

    Calling :py:meth:`unsubscribe` releases the underlying signal handler.
    Idempotent — multiple unsubscribes are safe. Mirrors
    :py:class:`desktop_isolation.Subscription`; kept local so this module
    stays self-contained (stdlib + typing only at the top).
    """

    def unsubscribe(self) -> None:
        ...


@runtime_checkable
class NMConnectivitySignals(Protocol):
    """Surface for subscribing to NetworkManager connectivity-change signals.

    Real impl (added in a later task) is a lazy-dasbus subscriber to NM's
    manager ``StateChanged`` signal; tests inject a fake. The ``callback``
    fires — on the GLib main loop in production — whenever NM's overall
    connectivity state changes. It takes no arguments: the signal is only a
    *trigger*; :py:class:`NMSignalMonitor` re-probes to confirm captivity.
    """

    def subscribe(self, callback: Callable[[], None]) -> Subscription:
        """Register a connectivity-change callback.

        Returns a :py:class:`Subscription` whose :py:meth:`unsubscribe` MUST
        be called to release the underlying handler.
        """
        ...


class NMSignalMonitor:
    """Event-driven captive-portal monitor.

    Subscribes to NetworkManager connectivity-change signals and, on each
    change, runs ``probe_fn`` on a short-lived daemon thread (never inline —
    the signal fires on the GLib main loop, and the probe does blocking
    network I/O). When ``probe_fn`` returns a portal URL, ``on_portal_detected``
    is invoked with it on that worker thread.

    Debounced: a single in-flight flag coalesces a burst of signals into at
    most one probe at a time, so rapid NM transitions can't spawn a probe
    storm. Same ``start()``/``stop()`` surface as :py:class:`Monitor`.
    """

    def __init__(
        self,
        signals: NMConnectivitySignals,
        probe_fn: Callable[[], Optional[str]],
        on_portal_detected: Callable[[str], None],
    ) -> None:
        self._signals = signals
        self._probe_fn = probe_fn
        self._on_portal_detected = on_portal_detected
        self._subscription: Optional[Subscription] = None
        self._lock = threading.Lock()
        self._probe_in_flight = False
        self._worker: Optional[threading.Thread] = None

    def start(self) -> None:
        """Subscribe to connectivity-change signals. Idempotent."""
        if self._subscription is not None:
            return
        self._subscription = self._signals.subscribe(self._on_connectivity_change)
        logger.info("NM-signal portal monitor started")

    def stop(self) -> None:
        """Unsubscribe from connectivity-change signals. Idempotent."""
        if self._subscription is not None:
            self._subscription.unsubscribe()
            self._subscription = None
        logger.info("NM-signal portal monitor stopped")

    def _on_connectivity_change(self) -> None:
        """Signal handler (runs on the GLib main loop in production).

        Must never raise (it fires from a signal handler) and must never
        probe inline. Spawns a worker only if no probe is already in flight.
        """
        try:
            with self._lock:
                if self._probe_in_flight:
                    return
                self._probe_in_flight = True
            worker = threading.Thread(
                target=self._probe_worker,
                name="gatepath-nm-probe",
                daemon=True,
            )
            self._worker = worker
            worker.start()
        except Exception as exc:  # noqa: BLE001
            # A failure spawning the worker must not escape into the signal
            # dispatcher; clear the flag so a later signal can retry.
            logger.warning("NM-signal probe dispatch error: %s", exc)
            with self._lock:
                self._probe_in_flight = False

    def _probe_worker(self) -> None:
        """Run the probe off the main loop; report a detected portal URL."""
        try:
            portal_url = self._probe_fn()
            if portal_url is not None:
                logger.info("Portal detected via NM-signal probe: %s", portal_url)
                self._on_portal_detected(portal_url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("NM-signal probe error: %s", exc)
        finally:
            with self._lock:
                self._probe_in_flight = False


# ── Production NMConnectivitySignals impl (lazy dasbus import) ───────────
#
# NM's manager emits ``StateChanged(state: u)`` whenever the overall NMState
# changes. We deliberately IGNORE the ``state`` argument: NMState numeric
# semantics vary and can't be exercised against real NM here, and the probe
# (portal_probe.probe) is the authoritative captive check. The signal is only
# a *trigger*; NMSignalMonitor re-probes on every transition to confirm.

NM_BUS_NAME = "org.freedesktop.NetworkManager"
NM_OBJECT_PATH = "/org/freedesktop/NetworkManager"


class DbusNMConnectivitySignals:
    """System-bus subscription to NM manager ``StateChanged``.

    Lazy dasbus import (mirrors :py:class:`desktop_isolation.DbusIsolationSignals`):
    constructing this with no injected bus raises ``RuntimeError`` if dasbus or
    the bus is unavailable, so :py:func:`start_nm_monitor` can fall back to
    polling. Not unit-tested against a real bus — same posture as
    ``DbusIsolationSignals`` (only the integration harness exercises live
    signal delivery); the subscribe/adapter/unsubscribe wiring IS unit-tested
    with a fake bus.
    """

    def __init__(self, bus=None) -> None:  # type: ignore[no-untyped-def]
        """Construct a signals subscriber.

        ``bus`` is an optional dasbus message-bus instance; ``None`` (the
        default) uses the system bus. Tests inject a fake bus to exercise the
        signal-subscription path headless.
        """
        if bus is None:
            try:
                from dasbus.connection import SystemMessageBus  # noqa: PLC0415
            except ImportError as exc:
                raise RuntimeError(f"dasbus not available: {exc}") from exc
            bus = SystemMessageBus()
        self._bus = bus
        self._proxy = None  # constructed on first subscribe

    def subscribe(self, callback: Callable[[], None]) -> Subscription:
        if self._proxy is None:
            self._proxy = self._bus.get_proxy(NM_BUS_NAME, NM_OBJECT_PATH)

        def adapter(state: int) -> None:
            # Drop the NMState arg: the probe is authoritative (see note above).
            callback()

        # dasbus signals: the proxy exposes the signal as an attribute that
        # supports `.connect(handler)` returning a handler ID; disconnect via
        # `.disconnect(id)`. Matches DbusIsolationSignals.
        signal = self._proxy.StateChanged
        handler_id = signal.connect(adapter)

        proxy = self._proxy

        class _DbusSubscription:
            def unsubscribe(self) -> None:
                try:
                    proxy.StateChanged.disconnect(handler_id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("signal disconnect raised: %s", exc)

        return _DbusSubscription()


def _make_probe_fn(probe_url: Optional[str] = None) -> Callable[[], Optional[str]]:
    """Return a probe function that wraps portal_probe.probe()."""
    from gatepath.portal_probe import CONNECTIVITY_CHECK_URL, probe  # noqa: PLC0415

    url = probe_url or CONNECTIVITY_CHECK_URL

    def _probe() -> Optional[str]:
        result = probe(url=url)
        if result.status == "portal":
            return result.portal_url
        return None

    return _probe


@runtime_checkable
class CaptiveInterfaceLookup(Protocol):
    """Returns the name of the WiFi interface currently flagged captive
    by NetworkManager, or ``None`` if there isn't one.

    Real impl is :py:class:`NMCaptiveInterfaceLookup` (lazy dasbus); tests
    inject a fake. Helper reads the same NM `Ip4Connectivity` property
    server-side, so what this returns matches what `helper.SetupCaptive`
    will accept (modulo race windows that the helper re-checks).
    """

    def get_captive_interface(self) -> Optional[str]:
        ...


class NMCaptiveInterfaceLookup:
    """Production captive-interface lookup via NetworkManager over dasbus.

    Lazy import: dasbus is loaded on the first :py:meth:`get_captive_interface`
    call so the module stays importable in environments without it (CI test
    nodes that don't ship dasbus, etc.).
    """

    def __init__(self) -> None:
        self._proxy = None

    def get_captive_interface(self) -> Optional[str]:
        try:
            if self._proxy is None:
                from dasbus.connection import SystemMessageBus  # noqa: PLC0415

                bus = SystemMessageBus()
                self._proxy = bus.get_proxy(
                    service_name="org.freedesktop.NetworkManager",
                    object_path="/org/freedesktop/NetworkManager",
                    interface_name="org.freedesktop.NetworkManager",
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("NetworkManager unreachable: %s", exc)
            return None

        try:
            device_paths = self._proxy.GetDevices()
        except Exception as exc:  # noqa: BLE001
            logger.warning("NM GetDevices failed: %s", exc)
            return None

        for path in device_paths:
            iface = _check_device_for_captive(path)
            if iface is not None:
                return iface
        return None


def _check_device_for_captive(device_path: str) -> Optional[str]:
    """Inspect one NM Device object. Returns the interface name iff the
    device is a WiFi device flagged `NM_CONNECTIVITY_PORTAL`. Returns
    ``None`` otherwise (wrong device type, not captive, lookup error)."""
    try:
        from dasbus.connection import SystemMessageBus  # noqa: PLC0415

        bus = SystemMessageBus()
        device = bus.get_proxy(
            service_name="org.freedesktop.NetworkManager",
            object_path=device_path,
            interface_name="org.freedesktop.NetworkManager.Device",
        )
        if device.DeviceType != NM_DEVICE_TYPE_WIFI:
            return None
        # NB: bare `Connectivity` does not exist on this interface (see the
        # module-level constant comment) — reading it raises InvalidArgs on
        # real NetworkManager >=1.16, which is swallowed by the except below
        # and silently returns None. Must read `Ip4Connectivity`.
        if device.Ip4Connectivity != NM_CONNECTIVITY_PORTAL:
            return None
        iface = device.Interface
        if isinstance(iface, str) and iface:
            return iface
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("NM device %s lookup failed: %s", device_path, exc)
        return None


def start_nm_monitor(
    on_portal_detected: Callable[[str], None],
    probe_url: Optional[str] = None,
    *,
    signals_factory: Optional[Callable[[], NMConnectivitySignals]] = None,
) -> "Monitor | NMSignalMonitor":
    """Start a captive-portal monitor, preferring the event-driven NM path.

    Tries to build an :py:class:`NMConnectivitySignals` source (by default the
    lazy-dasbus :py:class:`DbusNMConnectivitySignals`) and, on success, returns a
    started :py:class:`NMSignalMonitor` that re-probes on every NM connectivity
    transition. If building or starting that source fails for ANY reason (no
    dasbus, no system bus, no NetworkManager), falls back to the polling
    :py:class:`Monitor`. Both share the ``start()``/``stop()`` surface and are
    returned already started; callers (``app.py``) only hold the handle.

    ``signals_factory`` is injectable for headless tests: pass a factory
    returning a fake :py:class:`NMConnectivitySignals` to exercise the
    signal path, or one that raises to exercise the polling fallback. The
    public 2-arg form (``on_portal_detected``, ``probe_url``) is unchanged.

    Why not the ``org.freedesktop.portal.NetworkMonitor`` ``captive-portal``
    signal (reachable via the already-held ``portal.Desktop`` grant)?
    Investigated 2026-07-23 and deliberately NOT used: inside a Flatpak sandbox
    that portal value is NetworkManager's own connectivity classification merely
    proxied (GLib ``GNetworkMonitorPortal`` -> xdg-desktop-portal -> host
    ``GNetworkMonitor`` -> NM), so it is the SAME signal this monitor already
    subscribes to, just with extra hops and no better reliability. It is also
    strictly coarser: a global connectivity enum that does NOT name the captive
    WiFi interface, which the netns setup requires (see
    ``NMCaptiveInterfaceLookup``). So it would let us drop no permission
    (``--system-talk-name=org.freedesktop.NetworkManager`` stays required) while
    adding a redundant, coarser code path. Revisit only if Gatepath ever needs
    to run confined without ANY NM system-bus access — which the
    interface-lookup + netns-helper design currently precludes.
    """
    factory = signals_factory or DbusNMConnectivitySignals
    probe_fn = _make_probe_fn(probe_url)

    try:
        signals = factory()
        monitor = NMSignalMonitor(
            signals=signals,
            probe_fn=probe_fn,
            on_portal_detected=on_portal_detected,
        )
        monitor.start()
        logger.info("Portal monitor: event-driven NM-signal path active")
        return monitor
    except Exception as exc:  # noqa: BLE001 — NM/dasbus can fail many ways
        logger.info(
            "NM-signal path unavailable (%s); falling back to polling", exc
        )

    monitor = Monitor(
        probe_fn=probe_fn,
        on_portal_detected=on_portal_detected,
    )
    monitor.start()
    return monitor
