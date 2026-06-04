"""Tests for :py:mod:`gatepath.netns_client`.

The real dasbus path is exercised in integration tests with a running
helper; here we drive the client through a fake proxy that simulates
both success and the helper's typed error variants.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gatepath.netns_client import (
    ERROR_PREFIX,
    HelperUnavailable,
    LaunchPortalRefused,
    LaunchPortalSuccess,
    NetnsClient,
    RefusalReason,
    SetupRefused,
    SetupSuccess,
    SubprocessExit,
    TeardownRefused,
    TeardownSuccess,
    _dbus_error_name,
)


class _DBusError(Exception):
    """Test stand-in for dasbus's DBusError.

    Real dasbus exposes ``dbus_name`` on its error class; we mirror just
    that attribute so :py:func:`gatepath.netns_client._dbus_error_name`
    finds it via ``getattr``.
    """

    def __init__(self, dbus_name: str, message: str = "") -> None:
        super().__init__(message or dbus_name)
        self.dbus_name = dbus_name


class FakeProxy:
    """Hand-rolled fake satisfying :py:class:`HelperProxy`.

    Tests configure ``setup_result`` and ``teardown_result`` to either
    a return value (or ``None`` for teardown) or an exception to raise.
    """

    def __init__(self) -> None:
        self.setup_result: object = "/var/run/netns/gatepath"
        self.teardown_result: object = None
        self.launch_result: object = 12345  # default success PID
        self.setup_calls: list[str] = []
        self.teardown_calls: int = 0
        self.launch_calls: list[str] = []
        self.launch_display_calls: list[tuple[str, str, str]] = []

    def SetupCaptive(self, interface_name: str) -> str:  # noqa: N802
        self.setup_calls.append(interface_name)
        if isinstance(self.setup_result, BaseException):
            raise self.setup_result
        assert isinstance(self.setup_result, str)
        return self.setup_result

    def TeardownCaptive(self) -> None:  # noqa: N802
        self.teardown_calls += 1
        if isinstance(self.teardown_result, BaseException):
            raise self.teardown_result

    def LaunchPortal(  # noqa: N802
        self,
        portal_url: str,
        wayland_display: str = "",
        x_display: str = "",
        x_authority: str = "",
    ) -> int:
        self.launch_calls.append(portal_url)
        self.launch_display_calls.append((wayland_display, x_display, x_authority))
        if isinstance(self.launch_result, BaseException):
            raise self.launch_result
        assert isinstance(self.launch_result, int)
        return self.launch_result


# ── RefusalReason mapping ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("suffix", "expected"),
    [
        ("InvalidInterface", RefusalReason.INVALID_INTERFACE),
        ("NotCaptive", RefusalReason.NOT_CAPTIVE),
        ("Pending", RefusalReason.PENDING),
        ("Unauthorised", RefusalReason.UNAUTHORISED),
        ("BackendUnavailable", RefusalReason.BACKEND_UNAVAILABLE),
        ("KernelError", RefusalReason.KERNEL_ERROR),
        ("AlreadyActive", RefusalReason.ALREADY_ACTIVE),
        ("Throttled", RefusalReason.THROTTLED),
        ("NotActive", RefusalReason.NOT_ACTIVE),
        ("InvalidPortalUrl", RefusalReason.INVALID_PORTAL_URL),
        ("InvalidDisplayEnv", RefusalReason.INVALID_DISPLAY_ENV),
        ("NoActiveSession", RefusalReason.NO_ACTIVE_SESSION),
        ("SenderMismatch", RefusalReason.SENDER_MISMATCH),
        ("SpawnFailed", RefusalReason.SPAWN_FAILED),
        ("UnsupportedSecurity", RefusalReason.UNSUPPORTED_SECURITY),
    ],
)
def test_refusal_reason_maps_known_variants(suffix: str, expected: RefusalReason) -> None:
    assert RefusalReason.from_dbus_error_name(ERROR_PREFIX + suffix) == expected


def test_refusal_reason_unknown_suffix_maps_to_unknown() -> None:
    assert (
        RefusalReason.from_dbus_error_name(ERROR_PREFIX + "FuturePhase")
        == RefusalReason.UNKNOWN
    )


def test_refusal_reason_foreign_prefix_maps_to_unknown() -> None:
    assert (
        RefusalReason.from_dbus_error_name("org.freedesktop.DBus.Error.AccessDenied")
        == RefusalReason.UNKNOWN
    )


# ── cross-language drift guard (roadmap P1.1) ────────────────────────────

# RefusalReason lives in the privileged Rust helper crate; it is the source of
# truth for the wire names. The Python enum above must mirror every variant the
# helper can emit, or the UI silently degrades a typed refusal to UNKNOWN — which
# is exactly the `UnsupportedSecurity` drift this guard was added for.
#
# Scope: this guard checks *value coverage* (every Rust wire name has a matching
# Python enum value). It does NOT check *mapping correctness* — that a PascalCase
# suffix in from_dbus_error_name resolves to the RIGHT member;
# test_refusal_reason_maps_known_variants covers that for every known variant.
_RUST_LIB_RS = (
    Path(__file__).resolve().parents[2]
    / "desktop"
    / "gatepath-netns-helper"
    / "src"
    / "lib.rs"
)


def _rust_refusal_reason_wire_names() -> set[str]:
    """The snake_case names from `RefusalReason::as_str()` in lib.rs."""
    text = _RUST_LIB_RS.read_text(encoding="utf-8")
    # Narrow to the as_str() match block so other enums' arms can't leak in.
    # Assumes `impl RefusalReason` sits at 0-indent (so the fn closes at a
    # 4-space `}`); adjust the terminator if lib.rs moves it into a submodule.
    match = re.search(r"fn as_str\(self\)[^{]*\{(.*?)\n    \}", text, re.DOTALL)
    assert match, "could not locate RefusalReason::as_str() in lib.rs"
    return set(re.findall(r'=>\s*"([a-z_]+)"', match.group(1)))


def test_python_refusal_reasons_cover_every_rust_variant() -> None:
    if not _RUST_LIB_RS.exists():
        pytest.skip(f"Rust source not present at {_RUST_LIB_RS}")
    rust_names = _rust_refusal_reason_wire_names()
    assert rust_names, "parsed no RefusalReason::as_str() arms from lib.rs"
    python_names = {r.value for r in RefusalReason}
    missing = rust_names - python_names
    assert not missing, (
        f"Python RefusalReason omits variant(s) the helper can emit: {sorted(missing)}. "
        "Add each to the enum AND from_dbus_error_name (PascalCase suffix)."
    )


# ── _dbus_error_name extractor ───────────────────────────────────────────


def test_dbus_error_name_reads_attribute() -> None:
    exc = _DBusError(ERROR_PREFIX + "InvalidInterface", "interface not usable")
    assert _dbus_error_name(exc) == ERROR_PREFIX + "InvalidInterface"


def test_dbus_error_name_returns_none_for_plain_exception() -> None:
    assert _dbus_error_name(RuntimeError("transport blew up")) is None


def test_dbus_error_name_returns_none_for_empty_attribute() -> None:
    exc = _DBusError("", "no name")
    assert _dbus_error_name(exc) is None


# ── setup_captive ────────────────────────────────────────────────────────


def test_setup_captive_success_returns_netns_path() -> None:
    proxy = FakeProxy()
    proxy.setup_result = "/var/run/netns/gatepath"
    client = NetnsClient(proxy)

    result = client.setup_captive("wlan0")

    assert result == SetupSuccess(netns_path="/var/run/netns/gatepath")
    assert proxy.setup_calls == ["wlan0"]


@pytest.mark.parametrize(
    ("error_suffix", "expected_reason"),
    [
        ("InvalidInterface", RefusalReason.INVALID_INTERFACE),
        ("NotCaptive", RefusalReason.NOT_CAPTIVE),
        ("Pending", RefusalReason.PENDING),
        ("Unauthorised", RefusalReason.UNAUTHORISED),
        ("BackendUnavailable", RefusalReason.BACKEND_UNAVAILABLE),
        ("KernelError", RefusalReason.KERNEL_ERROR),
        ("AlreadyActive", RefusalReason.ALREADY_ACTIVE),
        ("Throttled", RefusalReason.THROTTLED),
    ],
)
def test_setup_captive_dbus_errors_map_to_typed_refusals(
    error_suffix: str, expected_reason: RefusalReason
) -> None:
    proxy = FakeProxy()
    proxy.setup_result = _DBusError(ERROR_PREFIX + error_suffix, "msg")
    client = NetnsClient(proxy)

    result = client.setup_captive("wlan0")

    assert isinstance(result, SetupRefused)
    assert result.reason == expected_reason
    assert "msg" in result.detail


def test_setup_captive_unknown_dbus_error_maps_to_unknown() -> None:
    proxy = FakeProxy()
    proxy.setup_result = _DBusError("org.freedesktop.DBus.Error.AccessDenied", "nope")
    client = NetnsClient(proxy)

    result = client.setup_captive("wlan0")

    assert isinstance(result, SetupRefused)
    assert result.reason == RefusalReason.UNKNOWN


def test_setup_captive_transport_error_maps_to_unknown() -> None:
    proxy = FakeProxy()
    proxy.setup_result = RuntimeError("connection reset by peer")
    client = NetnsClient(proxy)

    result = client.setup_captive("wlan0")

    assert isinstance(result, SetupRefused)
    assert result.reason == RefusalReason.UNKNOWN
    assert "connection reset" in result.detail


def test_setup_captive_empty_path_maps_to_unknown() -> None:
    proxy = FakeProxy()
    proxy.setup_result = ""
    client = NetnsClient(proxy)

    result = client.setup_captive("wlan0")

    assert isinstance(result, SetupRefused)
    assert result.reason == RefusalReason.UNKNOWN


# ── teardown_captive ─────────────────────────────────────────────────────


def test_teardown_captive_success() -> None:
    proxy = FakeProxy()
    client = NetnsClient(proxy)

    result = client.teardown_captive()

    assert isinstance(result, TeardownSuccess)
    assert proxy.teardown_calls == 1


def test_teardown_captive_not_active_maps_to_typed_refusal() -> None:
    proxy = FakeProxy()
    proxy.teardown_result = _DBusError(ERROR_PREFIX + "NotActive", "nothing to tear")
    client = NetnsClient(proxy)

    result = client.teardown_captive()

    assert isinstance(result, TeardownRefused)
    assert result.reason == RefusalReason.NOT_ACTIVE


def test_teardown_captive_kernel_error_maps_to_kernel_error() -> None:
    proxy = FakeProxy()
    proxy.teardown_result = _DBusError(ERROR_PREFIX + "KernelError", "destroy failed")
    client = NetnsClient(proxy)

    result = client.teardown_captive()

    assert isinstance(result, TeardownRefused)
    assert result.reason == RefusalReason.KERNEL_ERROR


def test_teardown_captive_unknown_error_maps_to_unknown() -> None:
    proxy = FakeProxy()
    proxy.teardown_result = RuntimeError("disconnected")
    client = NetnsClient(proxy)

    result = client.teardown_captive()

    assert isinstance(result, TeardownRefused)
    assert result.reason == RefusalReason.UNKNOWN


# ── HelperUnavailable surface ────────────────────────────────────────────


def test_connect_with_injected_bus_returns_client() -> None:
    """Pin: NetnsClient.connect(bus=...) accepts a dasbus-style bus
    object. Used by integration tests that spin up a session bus to
    exercise the wire protocol without root.
    """

    class StubBus:
        def __init__(self) -> None:
            self.proxy_for: list[tuple[str, str]] = []

        def get_proxy(self, bus_name: str, object_path: str):
            self.proxy_for.append((bus_name, object_path))
            return FakeProxy()

    bus = StubBus()
    client = NetnsClient.connect(bus=bus)

    assert isinstance(client, NetnsClient)
    # Pin the wire constants the production helper expects.
    from gatepath.netns_client import BUS_NAME, OBJECT_PATH  # noqa: PLC0415

    assert bus.proxy_for == [(BUS_NAME, OBJECT_PATH)]


def test_connect_raises_helper_unavailable_when_bus_proxy_fails() -> None:
    class FailingBus:
        def get_proxy(self, bus_name: str, object_path: str):
            raise RuntimeError("synthetic proxy failure")

    with pytest.raises(HelperUnavailable):
        NetnsClient.connect(bus=FailingBus())


def test_helper_unavailable_is_subclass_of_exception() -> None:
    # Pin the public surface: callers catch this specific class to
    # decide "fall back to static UX". A future refactor that changed
    # the base class would silently break those callers.
    assert issubclass(HelperUnavailable, Exception)


# ── Phase 5c.2 launch_portal ─────────────────────────────────────────────


def test_launch_portal_success_returns_pid() -> None:
    proxy = FakeProxy()
    proxy.launch_result = 4242
    client = NetnsClient(proxy)

    result = client.launch_portal("http://captive.example/login")

    assert result == LaunchPortalSuccess(pid=4242)
    assert proxy.launch_calls == ["http://captive.example/login"]
    # No display env passed → forwarded as empty strings (DESK-004).
    assert proxy.launch_display_calls == [("", "", "")]


def test_launch_portal_forwards_display_env() -> None:
    proxy = FakeProxy()
    client = NetnsClient(proxy)

    client.launch_portal(
        "http://captive.example/login",
        wayland_display="wayland-0",
        x_display=":0",
        x_authority="/home/u/.Xauthority",
    )

    assert proxy.launch_display_calls == [("wayland-0", ":0", "/home/u/.Xauthority")]


@pytest.mark.parametrize(
    ("error_suffix", "expected_reason"),
    [
        ("InvalidPortalUrl", RefusalReason.INVALID_PORTAL_URL),
        ("NoActiveSession", RefusalReason.NO_ACTIVE_SESSION),
        ("SenderMismatch", RefusalReason.SENDER_MISMATCH),
        ("SpawnFailed", RefusalReason.SPAWN_FAILED),
        ("Unauthorised", RefusalReason.UNAUTHORISED),
        ("BackendUnavailable", RefusalReason.BACKEND_UNAVAILABLE),
    ],
)
def test_launch_portal_dbus_errors_map_to_typed_refusals(
    error_suffix: str, expected_reason: RefusalReason
) -> None:
    proxy = FakeProxy()
    proxy.launch_result = _DBusError(ERROR_PREFIX + error_suffix, "msg")
    client = NetnsClient(proxy)

    result = client.launch_portal("http://captive.example/")

    assert isinstance(result, LaunchPortalRefused)
    assert result.reason == expected_reason
    assert "msg" in result.detail


def test_launch_portal_transport_error_maps_to_unknown() -> None:
    proxy = FakeProxy()
    proxy.launch_result = RuntimeError("connection reset by peer")
    client = NetnsClient(proxy)

    result = client.launch_portal("http://captive.example/")

    assert isinstance(result, LaunchPortalRefused)
    assert result.reason == RefusalReason.UNKNOWN


def test_launch_portal_zero_pid_maps_to_unknown() -> None:
    proxy = FakeProxy()
    proxy.launch_result = 0
    client = NetnsClient(proxy)

    result = client.launch_portal("http://captive.example/")

    assert isinstance(result, LaunchPortalRefused)
    assert result.reason == RefusalReason.UNKNOWN


def test_launch_portal_negative_pid_maps_to_unknown() -> None:
    proxy = FakeProxy()
    proxy.launch_result = -1
    client = NetnsClient(proxy)

    result = client.launch_portal("http://captive.example/")

    assert isinstance(result, LaunchPortalRefused)
    assert result.reason == RefusalReason.UNKNOWN


# ── SubprocessExit clean check ───────────────────────────────────────────


def test_subprocess_exit_is_clean_only_for_zero() -> None:
    assert SubprocessExit(pid=1, exit_code=0, signal_num=0).is_clean
    assert not SubprocessExit(pid=1, exit_code=1, signal_num=0).is_clean
    assert not SubprocessExit(pid=1, exit_code=-1, signal_num=9).is_clean
    assert not SubprocessExit(pid=1, exit_code=0, signal_num=15).is_clean
