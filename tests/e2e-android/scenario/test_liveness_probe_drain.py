"""`bound_begin` must not be measured for quiescence before a fired probe's
own connect() attempts could have finished.

`step_liveness_probe`'s poll loop fires `am start ... --es
gatepath.testvpn.action probe` and breaks as soon as the sentinel shows up in
the sink. But `am start` is fire-and-forget: it returns once the activity is
launched, not once its onCreate() (which spawns and `.join()`s a thread doing
up to `PROBE_COUNT` sequential `connect()` attempts, each with a
`CONNECT_TIMEOUT_MS` timeout) actually finishes. So the probe that triggered
capture can still be sending SYNs for up to `PROBE_COUNT * CONNECT_TIMEOUT_MS`
after the poll loop already moved on to the quiescence-settle measurement.

If the settle loop's FIRST sink read happens before that window has elapsed,
a straggler SYN from the very probe that just fired can land in the sink
*after* `bound_begin` gets marked and read as a bound-phase leak that never
happened — exactly what caused android-e2e to fail on PR #154 (4 sentinel
packets inside the bound window that were this straggler, not a WebView
leak).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCENARIO = Path(__file__).with_name("run-scenario.py")

_spec = importlib.util.spec_from_file_location("run_scenario", SCENARIO)
run_scenario = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_scenario)

# PROBE_COUNT (3) * CONNECT_TIMEOUT_MS (1500ms) from TestVpnControlActivity.kt.
# Deliberately hardcoded here, independent of run_scenario.PROBE_DRAIN_SEC, so
# this test proves the real timing property rather than merely that some
# constant with that name exists.
STRAGGLER_WORST_CASE_SEC = 4.5


def test_settle_measurement_waits_for_the_triggering_probe_to_finish(monkeypatch):
    """The poll loop fires exactly one probe and it's captured on the very
    first sink read -- the *best* case for the harness. Even then, the settle
    loop's first measurement must not run until that same probe's own
    connect() attempts could have finished."""
    clock = {"now": 0.0}

    def fake_monotonic() -> float:
        return clock["now"]

    def fake_sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr(run_scenario.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(run_scenario.time, "sleep", fake_sleep)

    probe_fire_times: list[float] = []
    pull_sink_call_times: list[float] = []
    mark_calls: list[tuple[str, float]] = []

    def fake_testvpn(serial: str, action: str, label: str | None = None) -> None:
        if action == "probe":
            probe_fire_times.append(clock["now"])

    def fake_pull_sink(serial: str) -> list[dict]:
        pull_sink_call_times.append(clock["now"])
        # Captured on the very first read -- the poll loop breaks having
        # fired exactly one probe.
        return [{"dst": run_scenario.SENTINEL_DST, "port": run_scenario.SENTINEL_PORT}]

    def fake_mark(serial: str, label: str) -> None:
        mark_calls.append((label, clock["now"]))

    monkeypatch.setattr(run_scenario, "_testvpn", fake_testvpn)
    monkeypatch.setattr(run_scenario, "_pull_sink", fake_pull_sink)
    monkeypatch.setattr(run_scenario, "_mark", fake_mark)

    result = run_scenario.step_liveness_probe({"serial": "emulator-fake"})

    assert result["captured"] is True
    assert len(probe_fire_times) == 1, "expected the poll loop to break after exactly one probe"
    last_fire = probe_fire_times[0]

    # The poll loop's own capturing read is pull_sink_call_times[0]; every
    # read after that belongs to the settle loop.
    assert len(pull_sink_call_times) >= 2, "settle loop never measured the sink"
    first_settle_call = pull_sink_call_times[1]

    assert first_settle_call - last_fire >= STRAGGLER_WORST_CASE_SEC, (
        f"settle measurement started only {first_settle_call - last_fire}s after "
        f"the triggering probe fired -- must wait >= {STRAGGLER_WORST_CASE_SEC}s "
        "for that probe's own connect() attempts to finish, or a straggler SYN "
        "lands after bound_begin and reads as a leak that never happened "
        "(PR #154)"
    )

    assert mark_calls and mark_calls[0][0] == "bound_begin"
    mark_time = mark_calls[0][1]
    assert mark_time >= last_fire + STRAGGLER_WORST_CASE_SEC
