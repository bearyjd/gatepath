"""D-Bus client for the gatepath-netns-helper privileged service (Phase 5c.1).

Pure-Python wrapper. Top-level imports stay stdlib + typing; dasbus is
imported lazily inside :py:meth:`NetnsClient.connect` so the test path
(passing a fake proxy) doesn't need it.

The wire protocol is exposed by the Rust helper crate at:
- bus name:   ``cc.grepon.Gatepath.NetNsHelper``
- object:     ``/cc/grepon/Gatepath/NetNsHelper``
- interface:  ``cc.grepon.Gatepath.NetNsHelper1``

Methods (all D-Bus names are PascalCase per zbus's default mapping):
- ``SetupCaptive(s) -> s`` — interface name in, netns path out, errors on refusal
- ``TeardownCaptive() -> ()`` — errors on refusal

Refusal errors land at ``cc.grepon.Gatepath.NetNsHelper.Error.<Variant>``;
this module maps them to :py:class:`RefusalReason` so callers branch on a
typed enum rather than a raw error string.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, Union, runtime_checkable

logger = logging.getLogger(__name__)

BUS_NAME = "cc.grepon.Gatepath.NetNsHelper"
OBJECT_PATH = "/cc/grepon/Gatepath/NetNsHelper"
INTERFACE = "cc.grepon.Gatepath.NetNsHelper1"
ERROR_PREFIX = "cc.grepon.Gatepath.NetNsHelper.Error."


class RefusalReason(Enum):
    """Stable identifiers matching the helper's audit-log strings.

    Values are the snake_case wire identifiers from
    ``gatepath_netns_helper::RefusalReason::as_str``. Adding a variant here
    is wire-compatible because callers should treat unknown variants as
    :py:attr:`UNKNOWN` (see :py:meth:`from_dbus_error_name`).
    """

    INVALID_INTERFACE = "invalid_interface"
    NOT_CAPTIVE = "not_captive"
    PENDING = "pending"
    UNAUTHORISED = "unauthorised"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    KERNEL_ERROR = "kernel_error"
    ALREADY_ACTIVE = "already_active"
    THROTTLED = "throttled"
    NOT_ACTIVE = "not_active"
    # Phase 5b.7 / 5c.2 — LaunchPortal refusal causes:
    INVALID_PORTAL_URL = "invalid_portal_url"
    NO_ACTIVE_SESSION = "no_active_session"
    SENDER_MISMATCH = "sender_mismatch"
    SPAWN_FAILED = "spawn_failed"
    UNKNOWN = "unknown"

    @classmethod
    def from_dbus_error_name(cls, error_name: str) -> "RefusalReason":
        """Map a fully-qualified D-Bus error name to a refusal reason.

        Unknown error names (helper rolling out a new variant we don't know
        about, or a malformed name) map to :py:attr:`UNKNOWN`. Callers
        should treat that as a generic refusal — never auto-grant.
        """
        if not error_name.startswith(ERROR_PREFIX):
            return cls.UNKNOWN
        suffix = error_name[len(ERROR_PREFIX) :]
        # PascalCase suffix → snake_case enum value, by way of the explicit
        # mapping below. Hand-coded so it stays an O(1) lookup and the
        # accepted variants are auditable in one place.
        mapping = {
            "InvalidInterface": cls.INVALID_INTERFACE,
            "NotCaptive": cls.NOT_CAPTIVE,
            "Pending": cls.PENDING,
            "Unauthorised": cls.UNAUTHORISED,
            "BackendUnavailable": cls.BACKEND_UNAVAILABLE,
            "KernelError": cls.KERNEL_ERROR,
            "AlreadyActive": cls.ALREADY_ACTIVE,
            "Throttled": cls.THROTTLED,
            "NotActive": cls.NOT_ACTIVE,
            "InvalidPortalUrl": cls.INVALID_PORTAL_URL,
            "NoActiveSession": cls.NO_ACTIVE_SESSION,
            "SenderMismatch": cls.SENDER_MISMATCH,
            "SpawnFailed": cls.SPAWN_FAILED,
        }
        return mapping.get(suffix, cls.UNKNOWN)


@dataclass(frozen=True)
class SetupSuccess:
    """Helper accepted the request and the netns is ready to spawn into."""

    netns_path: str


@dataclass(frozen=True)
class SetupRefused:
    """Helper refused the request. ``reason`` carries the typed cause."""

    reason: RefusalReason
    detail: str = ""


SetupResult = Union[SetupSuccess, SetupRefused]


@dataclass(frozen=True)
class TeardownSuccess:
    """Helper torn the active session down cleanly."""


@dataclass(frozen=True)
class TeardownRefused:
    """Helper refused the teardown.

    ``NOT_ACTIVE`` is the most common case — caller's session was already
    torn down (auto-teardown via name watch, double-teardown, etc.).
    Treat it as success-equivalent unless you specifically need to know.
    """

    reason: RefusalReason
    detail: str = ""


TeardownResult = Union[TeardownSuccess, TeardownRefused]


@dataclass(frozen=True)
class LaunchPortalSuccess:
    """Helper spawned the portal subprocess; PID is the new child."""

    pid: int


@dataclass(frozen=True)
class LaunchPortalRefused:
    """Helper refused the spawn. ``reason`` carries the typed cause.

    Common causes (5b.7):
      - ``NO_ACTIVE_SESSION`` — no prior `setup_captive` succeeded.
      - ``SENDER_MISMATCH`` — caller isn't the session owner.
      - ``INVALID_PORTAL_URL`` — URL failed RFC 3986 / scheme / control checks.
      - ``SPAWN_FAILED`` — fork/setns/execv kernel-level failure.
    """

    reason: RefusalReason
    detail: str = ""


LaunchPortalResult = Union[LaunchPortalSuccess, LaunchPortalRefused]


@dataclass(frozen=True)
class SubprocessExit:
    """Payload of the ``PortalSubprocessExited`` D-Bus signal.

    ``exit_code`` is ``-1`` if the subprocess was killed by a signal, in which
    case ``signal_num`` is the signal number. ``signal_num`` is ``0`` for a
    normal exit.
    """

    pid: int
    exit_code: int
    signal_num: int

    @property
    def is_clean(self) -> bool:
        """True iff the subprocess exited normally with code 0."""
        return self.exit_code == 0 and self.signal_num == 0


class HelperUnavailable(Exception):
    """The helper isn't reachable on the system bus.

    Likely causes (in rough order of frequency):
      - Helper package isn't installed (Flatpak-only deployment).
      - Helper binary exists but D-Bus activation failed.
      - System bus itself is unreachable (rare; running outside a session).

    Callers should fall back to the static-recovery UX, NOT auto-retry.
    """


@runtime_checkable
class HelperProxy(Protocol):
    """Minimal protocol the client needs from a D-Bus proxy.

    PascalCase method names match dasbus's default mapping of zbus's
    auto-generated wire names. Tests inject any object satisfying this
    shape; production uses the dasbus-generated proxy.
    """

    def SetupCaptive(self, interface_name: str) -> str:  # noqa: N802
        ...

    def TeardownCaptive(self) -> None:  # noqa: N802
        ...

    def LaunchPortal(self, portal_url: str) -> int:  # noqa: N802
        ...


class NetnsClient:
    """High-level client for ``gatepath-netns-helper``.

    Construct via :py:meth:`connect` (real D-Bus) or by passing a fake
    :py:class:`HelperProxy` for tests. Methods return typed result unions
    rather than raising on refusal — the helper's whole job is to refuse
    safely, so refusal isn't exceptional.

    :py:class:`HelperUnavailable` IS raised when the bus or helper itself
    is unreachable, since that's a different recovery path (degrade to
    static UX).
    """

    def __init__(self, proxy: HelperProxy) -> None:
        self._proxy = proxy

    @classmethod
    def connect(cls, *, bus=None) -> "NetnsClient":
        """Construct a real client backed by the system bus.

        ``bus`` is an optional dasbus message-bus instance — if ``None``
        the system bus is used. Integration tests inject a session bus
        backed by a private `dbus-daemon` so they can exercise the wire
        protocol without root.

        Raises :py:class:`HelperUnavailable` if dasbus or the system bus
        can't be reached, OR if the helper's bus name isn't registered.
        Caller should catch and degrade.
        """
        if bus is None:
            try:
                from dasbus.connection import SystemMessageBus  # noqa: PLC0415
            except ImportError as exc:
                raise HelperUnavailable(f"dasbus not available: {exc}") from exc
            try:
                bus = SystemMessageBus()
            except Exception as exc:  # noqa: BLE001 — dasbus raises a few kinds
                raise HelperUnavailable(f"system bus unreachable: {exc}") from exc

        try:
            proxy = bus.get_proxy(BUS_NAME, OBJECT_PATH)
        except Exception as exc:  # noqa: BLE001
            raise HelperUnavailable(f"helper proxy unreachable: {exc}") from exc

        return cls(proxy)

    def setup_captive(self, interface_name: str) -> SetupResult:
        """Ask the helper to move ``interface_name`` into the gatepath netns.

        On success returns :py:class:`SetupSuccess` carrying the netns path
        (typically ``/var/run/netns/gatepath``) for the caller to pass to
        ``nsenter --net=…`` when spawning the WebKit subprocess.

        On refusal returns :py:class:`SetupRefused` with the typed reason.
        Always user-gated — callers should never silently retry on
        ``UNAUTHORISED`` (user clicked Cancel) or ``THROTTLED`` (back off).
        """
        try:
            netns_path = self._proxy.SetupCaptive(interface_name)
        except Exception as exc:  # noqa: BLE001
            return _classify_setup_error(exc)

        if not isinstance(netns_path, str) or not netns_path:
            logger.error(
                "helper returned non-string netns_path: %r", netns_path,
            )
            return SetupRefused(
                reason=RefusalReason.UNKNOWN,
                detail="helper returned malformed netns path",
            )
        return SetupSuccess(netns_path=netns_path)

    def teardown_captive(self) -> TeardownResult:
        """Ask the helper to tear down the active netns.

        ``NOT_ACTIVE`` is treated as a refusal (typed) so callers can
        distinguish "we asked, nothing was there" from "teardown failed".
        Most paths should accept :py:class:`TeardownRefused` with reason
        :py:attr:`RefusalReason.NOT_ACTIVE` as a benign no-op.
        """
        try:
            self._proxy.TeardownCaptive()
        except Exception as exc:  # noqa: BLE001
            return _classify_teardown_error(exc)
        return TeardownSuccess()

    def launch_portal(self, portal_url: str) -> LaunchPortalResult:
        """Ask the helper to spawn a portal subprocess in the active netns.

        Returns :py:class:`LaunchPortalSuccess` carrying the spawned PID on
        success. The orchestrator subscribes to the helper's
        ``PortalSubprocessExited`` signal to know when the subprocess
        actually exits — the PID is informational here (logging, kill
        targeting if we add it later).

        On refusal returns :py:class:`LaunchPortalRefused` with the typed
        reason. Common refusals: ``NO_ACTIVE_SESSION`` (no prior
        ``setup_captive``), ``SENDER_MISMATCH`` (different bus client),
        ``INVALID_PORTAL_URL`` (failed RFC 3986 / scheme / control checks
        in the helper).
        """
        try:
            pid = self._proxy.LaunchPortal(portal_url)
        except Exception as exc:  # noqa: BLE001
            return _classify_launch_error(exc)

        if not isinstance(pid, int) or pid <= 0:
            logger.error("helper returned non-positive PID: %r", pid)
            return LaunchPortalRefused(
                reason=RefusalReason.UNKNOWN,
                detail=f"helper returned malformed pid: {pid!r}",
            )
        return LaunchPortalSuccess(pid=pid)


def _classify_setup_error(exc: BaseException) -> SetupRefused:
    """Convert a proxy exception into a typed :py:class:`SetupRefused`.

    Recognises D-Bus errors with our ``ERROR_PREFIX`` and maps them by
    name. Anything else (transport failure, unmapped error) becomes a
    generic ``UNKNOWN`` refusal — the helper deliberately surfaces every
    refusal as a typed error, so an unknown one is genuinely unexpected.
    """
    name = _dbus_error_name(exc)
    if name is None:
        return SetupRefused(reason=RefusalReason.UNKNOWN, detail=str(exc))
    return SetupRefused(
        reason=RefusalReason.from_dbus_error_name(name),
        detail=str(exc),
    )


def _classify_teardown_error(exc: BaseException) -> TeardownRefused:
    name = _dbus_error_name(exc)
    if name is None:
        return TeardownRefused(reason=RefusalReason.UNKNOWN, detail=str(exc))
    return TeardownRefused(
        reason=RefusalReason.from_dbus_error_name(name),
        detail=str(exc),
    )


def _classify_launch_error(exc: BaseException) -> LaunchPortalRefused:
    name = _dbus_error_name(exc)
    if name is None:
        return LaunchPortalRefused(reason=RefusalReason.UNKNOWN, detail=str(exc))
    return LaunchPortalRefused(
        reason=RefusalReason.from_dbus_error_name(name),
        detail=str(exc),
    )


def _dbus_error_name(exc: BaseException) -> str | None:
    """Read the D-Bus error name from a proxy exception, if present.

    dasbus's ``DBusError`` carries the fully-qualified error name on
    ``.dbus_name``. We don't import dasbus here so tests don't need it;
    instead we duck-type via ``getattr``. Anything else (transport
    failures, our own raised exceptions) returns ``None`` and the
    callers map that to :py:attr:`RefusalReason.UNKNOWN`.
    """
    name = getattr(exc, "dbus_name", None)
    if isinstance(name, str) and name:
        return name
    return None
