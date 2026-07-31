"""The diagnostic safety-net must not destroy the artifact under test.

`step_pull_logcat` deliberately captures the FULL post-clear buffer rather than
a `-t` window, and says why in its own comment: a bounded tail buried the
WebView lines under device spam in CI. The `finally` block then overwrote that
file with `logcat -d -t 3000` on every run — reintroducing precisely the
truncation the step exists to avoid.

It went unnoticed because the safety net is correct in the case it was written
for (a mid-scenario failure, where pull_logcat never runs) and merely
destructive in the case it was not (a completed run, where pull_logcat already
wrote the full buffer). Both runs of the off-domain positive control landed on
the destructive path; one happened to keep its evidence inside the last 3000
lines and passed, the other did not and failed. Same code, same app behaviour.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCENARIO = Path(__file__).with_name("run-scenario.py")

_spec = importlib.util.spec_from_file_location("run_scenario", SCENARIO)
run_scenario = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_scenario)


def test_fallback_skipped_when_pull_logcat_already_captured(tmp_path):
    """A completed run must keep the full buffer pull_logcat wrote."""
    path = tmp_path / "logcat.txt"
    path.write_text("GatepathWebView: the evidence\n" + "spam\n" * 10_000)
    assert run_scenario.should_write_fallback_logcat(path) is False


def test_fallback_written_when_scenario_died_before_pull_logcat(tmp_path):
    """The case the safety net exists for: no artifact at all."""
    assert run_scenario.should_write_fallback_logcat(tmp_path / "logcat.txt") is True


def test_fallback_written_when_existing_capture_is_empty(tmp_path):
    """An empty file is not a capture — adb can return nothing on a wedged device."""
    path = tmp_path / "logcat.txt"
    path.write_text("")
    assert run_scenario.should_write_fallback_logcat(path) is True


def test_pull_logcat_still_captures_the_unbounded_buffer():
    """Guard the other half: the full-buffer capture must not regress to -t.

    If this ever becomes `logcat -d -t N`, the off-domain and sentinel positive
    controls go flaky again with no other symptom.
    """
    source = SCENARIO.read_text()
    body = source.split("def step_pull_logcat(", 1)[1].split("\ndef ", 1)[0]
    # Inspect the adb invocation only — the surrounding comment legitimately
    # mentions the `-t` window it warns against.
    calls = [ln for ln in body.splitlines() if "adb_helper.shell(" in ln]
    assert calls, "pull_logcat no longer shells out to adb"
    assert any('"logcat -d"' in ln for ln in calls), (
        "pull_logcat must capture the full buffer"
    )
    assert not any("logcat -d -t" in ln for ln in calls), (
        "pull_logcat must not use a bounded tail window"
    )
