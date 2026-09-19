"""Shape of the two scenario modes — checked without an emulator."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "run_scenario", Path(__file__).resolve().parent / "run-scenario.py"
)
rs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rs)  # type: ignore[union-attr]


def test_excluding_mode_signs_in_via_the_monitor_not_the_debug_intent():
    names = rs.step_names(rs.STEPS_EXCLUDING)
    assert "launch_debug_portal" not in names
    assert names.index("wait_confinement_state") < names.index("wait_portal_screen")
    assert "submit_login" in names and "wait_validated" in names


def test_covering_mode_never_reaches_the_portal_or_login():
    names = rs.step_names(rs.STEPS_COVERING)
    assert "submit_login" not in names
    assert "wait_validated" not in names
    assert "wait_confinement_state" in names
    assert "pull_confinement_state" in names


def test_both_modes_install_the_test_vpn_and_pull_the_sink():
    for steps in (rs.STEPS_COVERING, rs.STEPS_EXCLUDING):
        names = rs.step_names(steps)
        assert "install_testvpn" in names
        assert "pull_vpn_sink" in names
        assert "pull_audit_log" in names


def test_excluding_mode_lays_no_sink_markers():
    """Gatepath is outside the VPN in `excluding` mode, so the sink is not an
    oracle for it -- no phase-marker steps should run at all."""
    names = rs.step_names(rs.STEPS_EXCLUDING)
    assert "mark_bound_end" not in names
    assert "liveness_probe" not in names
    assert "settle_covering" not in names


def test_covering_mode_lays_bound_end_via_settle_not_a_separate_step():
    """settle_covering itself marks bound_end (closing the window it opened
    via liveness_probe's bound_begin) -- there is no separate mark_bound_end
    step on the covering side."""
    names = rs.step_names(rs.STEPS_COVERING)
    assert "settle_covering" in names
    assert "mark_bound_end" not in names
