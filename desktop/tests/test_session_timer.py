"""Tests for gatepath.session_timer — the 10-minute portal timeout primitive."""

from __future__ import annotations

import pytest

from gatepath.portal_session import SESSION_TIMEOUT_SECONDS
from gatepath.session_timer import FakeScheduler, SessionTimer


class TestSessionTimer:
    def test_default_timeout_matches_constant(self) -> None:
        sched = FakeScheduler()
        timer = SessionTimer(sched)
        timer.start(lambda: None)
        assert sched.pending_count == 1
        # FakeScheduler exposes seconds via the internal tuple.
        seconds = sched._scheduled[0][1]
        assert seconds == SESSION_TIMEOUT_SECONDS == 600

    def test_start_arms_timer(self) -> None:
        sched = FakeScheduler()
        timer = SessionTimer(sched, timeout_seconds=10)
        assert not timer.is_armed
        timer.start(lambda: None)
        assert timer.is_armed
        assert sched.pending_count == 1

    def test_cancel_disarms_timer(self) -> None:
        sched = FakeScheduler()
        timer = SessionTimer(sched, timeout_seconds=10)
        timer.start(lambda: None)
        timer.cancel()
        assert not timer.is_armed
        assert sched.pending_count == 0

    def test_cancel_when_not_armed_is_noop(self) -> None:
        sched = FakeScheduler()
        timer = SessionTimer(sched)
        timer.cancel()  # should not raise
        assert sched.pending_count == 0

    def test_start_twice_replaces_not_doubles(self) -> None:
        sched = FakeScheduler()
        timer = SessionTimer(sched, timeout_seconds=10)
        timer.start(lambda: None)
        timer.start(lambda: None)
        assert sched.pending_count == 1, "second start() must cancel the first"

    def test_callback_fires_via_scheduler(self) -> None:
        sched = FakeScheduler()
        timer = SessionTimer(sched, timeout_seconds=10)
        fired = []
        timer.start(lambda: fired.append("now"))
        sched.fire_all()
        assert fired == ["now"]

    def test_cancel_after_fire_is_noop(self) -> None:
        sched = FakeScheduler()
        timer = SessionTimer(sched, timeout_seconds=10)
        timer.start(lambda: None)
        sched.fire_all()
        # The handle was popped from the FakeScheduler when fired, but
        # SessionTimer still holds it. cancel() must not raise.
        timer.cancel()


class TestFakeScheduler:
    def test_schedule_returns_unique_handles(self) -> None:
        sched = FakeScheduler()
        h1 = sched.schedule(1, lambda: None)
        h2 = sched.schedule(1, lambda: None)
        assert h1 != h2

    def test_cancel_unknown_handle_is_noop(self) -> None:
        sched = FakeScheduler()
        sched.cancel("not-a-real-handle")  # should not raise
        assert sched.pending_count == 0

    def test_fire_all_returns_count(self) -> None:
        sched = FakeScheduler()
        sched.schedule(1, lambda: None)
        sched.schedule(1, lambda: None)
        sched.schedule(1, lambda: None)
        assert sched.fire_all() == 3
        assert sched.pending_count == 0
