"""Tests for :py:mod:`gatepath.desktop_isolation`.

The orchestrator is exercised through fakes for both `NetnsClient` and
`IsolationSignals` — no system bus, no real subprocesses. The real path
is integration-tested in PR-C against a docker-compose mock-captive
setup.
"""

from __future__ import annotations

import threading
from typing import Optional

import pytest

from gatepath.desktop_isolation import (
    DEFAULT_WAIT_TIMEOUT_SECONDS,
    DesktopIsolation,
    DisengageRefused,
    DisengageSuccess,
    EngageRefused,
    EngageSuccess,
    IsolationSignals,
    Subscription,
    WaitInterrupted,
    WaitTimeout,
)
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


# ── Test fakes ───────────────────────────────────────────────────────────


class FakeNetnsClient(NetnsClient):
    """Subclass of NetnsClient that bypasses the proxy entirely.

    Tests poke `setup_result`, `launch_result`, `teardown_result` directly.
    Inheriting from NetnsClient (rather than mock-stubbing) keeps the
    type annotation on DesktopIsolation honest.
    """

    def __init__(self) -> None:
        # Do not call super().__init__ — we don't want a real proxy.
        self.setup_result: object = SetupSuccess(netns_path="/var/run/netns/gatepath")
        self.launch_result: object = LaunchPortalSuccess(pid=4242)
        self.teardown_result: object = TeardownSuccess()
        self.setup_calls: list[str] = []
        self.launch_calls: list[str] = []
        self.teardown_calls: int = 0

    def setup_captive(self, interface_name: str):  # type: ignore[override]
        self.setup_calls.append(interface_name)
        assert isinstance(self.setup_result, (SetupSuccess, SetupRefused))
        return self.setup_result

    def launch_portal(self, portal_url: str):  # type: ignore[override]
        self.launch_calls.append(portal_url)
        assert isinstance(self.launch_result, (LaunchPortalSuccess, LaunchPortalRefused))
        return self.launch_result

    def teardown_captive(self):  # type: ignore[override]
        self.teardown_calls += 1
        assert isinstance(self.teardown_result, (TeardownSuccess, TeardownRefused))
        return self.teardown_result


class FakeSubscription:
    def __init__(self, signals: "FakeIsolationSignals") -> None:
        self._signals = signals
        self.unsubscribed = False

    def unsubscribe(self) -> None:
        self.unsubscribed = True
        self._signals._active_callback = None


class FakeIsolationSignals(IsolationSignals):
    """Records subscriptions and lets tests fire exits manually."""

    def __init__(self) -> None:
        self._active_callback = None
        self.subscribe_count = 0

    def subscribe(self, callback) -> Subscription:  # type: ignore[no-untyped-def]
        self._active_callback = callback
        self.subscribe_count += 1
        return FakeSubscription(self)

    def fire_exit(self, exit_payload: SubprocessExit) -> None:
        if self._active_callback is not None:
            self._active_callback(exit_payload)


def _make_iso() -> tuple[DesktopIsolation, FakeNetnsClient, FakeIsolationSignals]:
    client = FakeNetnsClient()
    signals = FakeIsolationSignals()
    return DesktopIsolation(client, signals), client, signals


# ── engage ──────────────────────────────────────────────────────────────


def test_engage_success_returns_pid_and_netns_path() -> None:
    iso, client, signals = _make_iso()

    result = iso.engage("http://captive.example/", "wlan0")

    assert result == EngageSuccess(netns_path="/var/run/netns/gatepath", pid=4242)
    assert client.setup_calls == ["wlan0"]
    assert client.launch_calls == ["http://captive.example/"]
    assert signals.subscribe_count == 1


def test_engage_setup_refused_does_not_attempt_launch() -> None:
    iso, client, signals = _make_iso()
    client.setup_result = SetupRefused(reason=RefusalReason.NOT_CAPTIVE, detail="x")

    result = iso.engage("http://captive.example/", "wlan0")

    assert isinstance(result, EngageRefused)
    assert result.reason == RefusalReason.NOT_CAPTIVE
    assert result.stage == "setup"
    assert client.launch_calls == []
    # Subscription cleaned up — caller doesn't need to disengage.
    assert signals._active_callback is None


def test_engage_launch_refused_leaves_subscription_for_disengage() -> None:
    iso, client, signals = _make_iso()
    client.launch_result = LaunchPortalRefused(
        reason=RefusalReason.INVALID_PORTAL_URL, detail="bad scheme"
    )

    result = iso.engage("javascript:alert(1)", "wlan0")

    assert isinstance(result, EngageRefused)
    assert result.reason == RefusalReason.INVALID_PORTAL_URL
    assert result.stage == "launch"
    # Setup succeeded → caller must call disengage to tear it down.
    assert client.setup_calls == ["wlan0"]
    # Subscription still installed — disengage will clean it up.
    assert signals._active_callback is not None


def test_engage_subscribes_before_launch() -> None:
    """Pin: signal subscription must complete before LaunchPortal is called.

    Otherwise a fast-failing subprocess could exit between launch and
    subscribe — we'd miss the exit signal entirely.
    """
    iso, client, signals = _make_iso()

    subscribe_observed_during_launch = []

    def launch_with_check(portal_url: str):
        subscribe_observed_during_launch.append(signals._active_callback is not None)
        return LaunchPortalSuccess(pid=42)

    client.launch_portal = launch_with_check  # type: ignore[method-assign]
    iso.engage("http://captive.example/", "wlan0")

    assert subscribe_observed_during_launch == [True]


# ── wait_for_subprocess ─────────────────────────────────────────────────


def test_wait_returns_subprocess_exit_when_signal_fires() -> None:
    iso, _client, signals = _make_iso()
    iso.engage("http://captive.example/", "wlan0")

    payload = SubprocessExit(pid=4242, exit_code=0, signal_num=0)
    # Fire on a separate thread so wait_for_subprocess is actually blocked.
    fire_thread = threading.Thread(target=lambda: signals.fire_exit(payload))
    fire_thread.start()
    result = iso.wait_for_subprocess(timeout_seconds=2.0)
    fire_thread.join()

    assert result == payload


def test_wait_returns_timeout_when_signal_never_arrives() -> None:
    iso, _client, _signals = _make_iso()
    iso.engage("http://captive.example/", "wlan0")

    result = iso.wait_for_subprocess(timeout_seconds=0.2)

    assert isinstance(result, WaitTimeout)


def test_wait_returns_interrupted_when_cancelled() -> None:
    iso, _client, _signals = _make_iso()
    iso.engage("http://captive.example/", "wlan0")

    fire_thread = threading.Thread(target=iso.cancel_wait)
    fire_thread.start()
    result = iso.wait_for_subprocess(timeout_seconds=2.0)
    fire_thread.join()

    assert isinstance(result, WaitInterrupted)


def test_wait_after_subprocess_already_exited_returns_subprocess_exit() -> None:
    """If the signal fires before wait_for_subprocess is called, the
    payload is captured and returned on the next call.
    """
    iso, _client, signals = _make_iso()
    iso.engage("http://captive.example/", "wlan0")
    payload = SubprocessExit(pid=4242, exit_code=0, signal_num=0)
    signals.fire_exit(payload)

    result = iso.wait_for_subprocess(timeout_seconds=0.5)

    assert result == payload


def test_default_timeout_matches_session_ceiling() -> None:
    # Session timeout is 600s in portal_session; pin parity here.
    assert DEFAULT_WAIT_TIMEOUT_SECONDS == 600.0


# ── disengage ───────────────────────────────────────────────────────────


def test_disengage_success_unsubscribes_and_tears_down() -> None:
    iso, client, signals = _make_iso()
    iso.engage("http://captive.example/", "wlan0")

    result = iso.disengage()

    assert isinstance(result, DisengageSuccess)
    assert client.teardown_calls == 1
    assert signals._active_callback is None


def test_disengage_after_launch_refused_still_tears_down() -> None:
    iso, client, signals = _make_iso()
    client.launch_result = LaunchPortalRefused(
        reason=RefusalReason.SPAWN_FAILED, detail="kernel said no"
    )
    iso.engage("http://captive.example/", "wlan0")

    result = iso.disengage()

    assert isinstance(result, DisengageSuccess)
    assert client.teardown_calls == 1
    assert signals._active_callback is None


def test_disengage_when_not_active_returns_typed_refusal() -> None:
    iso, client, _signals = _make_iso()
    client.teardown_result = TeardownRefused(
        reason=RefusalReason.NOT_ACTIVE, detail="nothing to tear"
    )

    result = iso.disengage()

    assert isinstance(result, DisengageRefused)
    assert result.reason == RefusalReason.NOT_ACTIVE


def test_disengage_with_kernel_error_returns_typed_refusal() -> None:
    iso, client, _signals = _make_iso()
    client.teardown_result = TeardownRefused(
        reason=RefusalReason.KERNEL_ERROR, detail="busy"
    )
    iso.engage("http://captive.example/", "wlan0")

    result = iso.disengage()

    assert isinstance(result, DisengageRefused)
    assert result.reason == RefusalReason.KERNEL_ERROR


def test_engage_disengage_cycle_can_repeat() -> None:
    """Pin: orchestrator can be reused for multiple captive sessions."""
    iso, client, signals = _make_iso()

    iso.engage("http://captive1.example/", "wlan0")
    payload1 = SubprocessExit(pid=4242, exit_code=0, signal_num=0)
    signals.fire_exit(payload1)
    result1 = iso.wait_for_subprocess(timeout_seconds=0.5)
    iso.disengage()

    iso.engage("http://captive2.example/", "wlan0")
    # Fresh subscribe count.
    assert signals.subscribe_count == 2
    iso.disengage()

    assert client.setup_calls == ["wlan0", "wlan0"]
    assert client.teardown_calls == 2
    assert isinstance(result1, SubprocessExit)
