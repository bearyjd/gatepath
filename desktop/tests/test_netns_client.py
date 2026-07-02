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
#
# The privileged Rust helper is the source of truth for the wire contract. Two
# Rust enums define it, and the Python `RefusalReason` + `from_dbus_error_name`
# must mirror them or the UI silently degrades a typed refusal to UNKNOWN — the
# exact `UnsupportedSecurity` drift (#51) this guard exists for.
#
#   * `HelperError` (dbus_service.rs) — the `zbus::DBusError` enum. ITS variant
#     names (PascalCase, under the `.Error.` prefix) are what literally land on
#     the bus. This is the real source of truth for wire names.
#   * `RefusalReason::as_str()` (lib.rs) — the snake_case *audit-log* spelling.
#     1:1 with HelperError except teardown-only `NotActive` (no RefusalReason).
#
# These parsers are deliberately lightweight; the heavier, more robust pattern is
# a shared checked-in schema both languages validate against — see
# `schema-parity.yml`, which already does this for the audit-log schema. Extending
# that to the D-Bus error names is the open "bigger drift guard" in ROADMAP P1.1.
_RUST_LIB_RS = (
    Path(__file__).resolve().parents[2]
    / "desktop"
    / "gatepath-netns-helper"
    / "src"
    / "lib.rs"
)
_DBUS_SERVICE_RS = _RUST_LIB_RS.parent / "dbus_service.rs"


def _pascal_to_snake(name: str) -> str:
    """Convert a PascalCase wire suffix to its snake_case audit spelling.

    Relies on the helper's 1:1 PascalCase↔snake_case convention: every word
    boundary is a single leading capital, with NO multi-letter acronyms
    (`InvalidPortalUrl`, never `InvalidPortalURL`). If that convention is ever
    broken on the Rust side, this and `RefusalReason::as_str()` must change
    together — and `test_helper_error_and_refusal_reason_stay_in_lockstep`
    will fail loudly until they do.
    """
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _rust_refusal_reason_wire_names() -> set[str]:
    """The snake_case names from `RefusalReason::as_str()` in lib.rs."""
    text = _RUST_LIB_RS.read_text(encoding="utf-8")
    # Narrow to the as_str() match block so other enums' arms can't leak in.
    # Assumes `impl RefusalReason` sits at 0-indent (so the fn closes at a
    # 4-space `}`); adjust the terminator if lib.rs moves it into a submodule.
    match = re.search(r"fn as_str\(self\)[^{]*\{(.*?)\n    \}", text, re.DOTALL)
    assert match, (
        "could not locate RefusalReason::as_str() in lib.rs — did the fn move, "
        "change signature, or stop being 4-space-indented under `impl RefusalReason`?"
    )
    return set(re.findall(r'=>\s*"([a-z0-9_]+)"', match.group(1)))


def _rust_helper_error_wire_suffixes() -> set[str]:
    """The PascalCase wire suffixes from the `HelperError` enum in dbus_service.rs.

    These are the actual `com.ventouxlabs.Gatepath.NetNsHelper.Error.<Suffix>` names
    the helper puts on the bus. Excludes the `#[zbus(error)]` transport
    passthrough (`ZBus`), which zbus serialises as the standard
    `org.freedesktop.DBus.Error.*` — not one of our typed names, and correctly
    UNKNOWN client-side.
    """
    text = _DBUS_SERVICE_RS.read_text(encoding="utf-8")
    match = re.search(r"pub enum HelperError\s*\{(.*?)\n\}", text, re.DOTALL)
    assert match, (
        "could not locate `pub enum HelperError { … }` in dbus_service.rs — did "
        "the enum move, stop being 0-indented, or change shape? Update this parser "
        "(or migrate to a shared schema; see schema-parity.yml)."
    )
    suffixes: set[str] = set()
    pending_zbus_error = False
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("#[zbus(error)]"):
            pending_zbus_error = True
            continue
        # Match any variant shape — tuple `Name(...)`, struct `Name {...}`, or
        # unit `Name,` — so a future non-tuple variant can't be silently dropped
        # (which would let the round-trip guard under-cover). Doc/attribute/field/
        # brace lines never start with an uppercase identifier, so they don't match.
        variant = re.match(r"([A-Z][A-Za-z0-9]*)\s*(?:[({,]|$)", stripped)
        if variant:
            if not pending_zbus_error:
                suffixes.add(variant.group(1))
            pending_zbus_error = False
    return suffixes


def test_python_round_trips_every_helper_wire_error() -> None:
    """Every wire error the helper can emit must round-trip to a concrete reason.

    THE drift guard. Unlike a value-membership check, this exercises the actual
    `from_dbus_error_name` *mapping*, so it catches the exact #51 failure — an
    enum value present but its mapping entry absent → UI saw UNKNOWN — AND
    wrong-mappings. Source of truth is `HelperError` (what lands on the bus),
    which also covers `NotActive` (no `RefusalReason::as_str()` arm).
    """
    if not _DBUS_SERVICE_RS.exists():
        pytest.skip(f"Rust source not present at {_DBUS_SERVICE_RS}")
    suffixes = _rust_helper_error_wire_suffixes()
    assert suffixes, "parsed no HelperError variants from dbus_service.rs"
    resolved = {
        s: RefusalReason.from_dbus_error_name(ERROR_PREFIX + s) for s in suffixes
    }

    unmapped = sorted(s for s, r in resolved.items() if r is RefusalReason.UNKNOWN)
    assert not unmapped, (
        f"from_dbus_error_name degrades helper wire error(s) to UNKNOWN: {unmapped}. "
        "The helper can emit these but the UI would treat them as a generic refusal. "
        "Add each to RefusalReason AND the from_dbus_error_name mapping (PascalCase suffix)."
    )

    # Mapping correctness, not just presence: each PascalCase suffix must resolve
    # to the member whose value is its snake_case form (catches a swapped entry,
    # e.g. NotActive → KERNEL_ERROR).
    mismatched = {
        s: r.value for s, r in resolved.items() if r.value != _pascal_to_snake(s)
    }
    assert not mismatched, (
        f"from_dbus_error_name resolves wire name(s) to the wrong reason: {mismatched}. "
        "Each PascalCase suffix must map to the member whose value is its snake_case form."
    )


def test_drift_guard_machinery_is_not_vacuous() -> None:
    """Prove the round-trip guard has teeth rather than passing vacuously.

    A synthetic name the helper can't emit must resolve to UNKNOWN (so the
    "not UNKNOWN" assertion above is meaningful), and the HelperError parser must
    yield exactly the expected number of variants (so a regex that silently
    under-matches can't make the round-trip guard green by under-covering).
    """
    assert (
        RefusalReason.from_dbus_error_name(ERROR_PREFIX + "TotallyNotARealVariant")
        is RefusalReason.UNKNOWN
    )
    if not (_DBUS_SERVICE_RS.exists() and _RUST_LIB_RS.exists()):
        pytest.skip("Rust source not present")
    # Parser integrity: HelperError must yield exactly one more wire name than
    # RefusalReason::as_str() has arms — the teardown-only NotActive. Pinning the
    # exact relationship (not a loose floor) trips on a single silently-dropped
    # variant, the one false-green the loose `>= N` check would have allowed.
    helper = _rust_helper_error_wire_suffixes()
    refusal = _rust_refusal_reason_wire_names()
    assert len(helper) == len(refusal) + 1, (
        f"HelperError parser yielded {len(helper)} wire name(s); expected "
        f"{len(refusal) + 1} (RefusalReason::as_str arms + teardown-only NotActive). "
        "A silently-dropped variant would make the round-trip guard under-cover."
    )


def test_helper_error_and_refusal_reason_stay_in_lockstep() -> None:
    """The two Rust enums that define the wire contract must agree.

    Every `RefusalReason::as_str()` name has a matching `HelperError` variant
    (so `HelperError::from_refusal` stays total) and vice-versa — except
    teardown-only `NotActive`, which is a typed error with no refusal. Pins both
    the lockstep and the PascalCase↔snake_case convention `_pascal_to_snake`
    relies on.
    """
    if not (_RUST_LIB_RS.exists() and _DBUS_SERVICE_RS.exists()):
        pytest.skip("Rust source not present")
    refusal_snake = _rust_refusal_reason_wire_names()
    helper_snake = {_pascal_to_snake(s) for s in _rust_helper_error_wire_suffixes()}

    helper_only = helper_snake - refusal_snake
    assert helper_only == {"not_active"}, (
        "HelperError carries typed error(s) with no RefusalReason: "
        f"{sorted(helper_only)} (expected only {{'not_active'}}). "
        "Either add the matching RefusalReason or confirm it is teardown-only."
    )
    refusal_only = refusal_snake - helper_snake
    assert not refusal_only, (
        f"RefusalReason(s) with no HelperError variant: {sorted(refusal_only)}. "
        "HelperError::from_refusal would not be total — add the variant."
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
