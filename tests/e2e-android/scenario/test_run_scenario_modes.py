"""Shape of the two scenario modes — checked without an emulator."""
from __future__ import annotations

import importlib.util
import socket
import sys
import threading
import time
import urllib.error
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "run_scenario", Path(__file__).resolve().parent / "run-scenario.py"
)
rs = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(rs)  # type: ignore[union-attr]

# Repo root is three parents up from tests/e2e-android/scenario/<this file>.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from mockportal.server import build_server  # noqa: E402


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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


def test_submit_login_does_not_follow_the_emulator_facing_redirect():
    """The mock's /login answers 302 to an emulator-facing advertised_host
    (192.0.2.1, TEST-NET-1 -- deliberately unroutable from this process, the
    same shape as the real CI redirect to 10.0.2.2). step_submit_login must
    treat that 302 as the success signal without ever trying to follow it --
    following it would hang until the per-request timeout and then raise
    urllib.error.URLError instead of completing quickly."""
    port = _free_port()
    server, _state = build_server(
        host="127.0.0.1", port=port, complete_after=1000, advertised_host="192.0.2.1"
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"

        # Before login the session is unauthenticated and complete_after=1000
        # keeps it captive, so the probe redirects -- confirm _http surfaces
        # that as HTTPError(302) rather than following it (the opener's
        # no-redirect behavior, checked directly before submit_login changes
        # server state by authenticating the session).
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            rs._http(f"{base}/generate_204")
        assert exc_info.value.code == 302

        start = time.monotonic()
        result = rs.step_submit_login({"mode": "host-post", "mockportal_from_host_url": base})
        elapsed = time.monotonic() - start
        assert elapsed < 3, (
            f"step_submit_login took {elapsed:.1f}s -- it followed the "
            "unroutable emulator-facing redirect instead of observing it"
        )
        assert result == {"mode": "host-post", "outcome": "success"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_start_test_vpn_raises_on_an_am_start_refusal(monkeypatch):
    """`am start` exits 0 and prints the denial, so `check=` cannot catch it.
    A swallowed refusal surfaced three steps later as 'VPN never established'
    (PR #168 CI, after the control activity was made non-exported)."""
    denial = (
        "Starting: Intent { cmp=com.ventouxlabs.gatepath.testvpn/.TestVpnControlActivity }\n"
        "java.lang.SecurityException: Permission Denial: starting Intent { ... } "
        "from null (pid=1234, uid=2000) not exported from uid 10154"
    )
    monkeypatch.setattr(rs.adb_helper, "shell_full", lambda *a, **k: (denial, ""))
    with pytest.raises(RuntimeError, match="refused"):
        rs.step_start_test_vpn({"serial": "emulator-fake", "vpn_mode": "covering"})


def test_start_test_vpn_records_the_launch_output(monkeypatch):
    launched = "Starting: Intent { cmp=com.ventouxlabs.gatepath.testvpn/.TestVpnControlActivity }"
    monkeypatch.setattr(rs.adb_helper, "shell_full", lambda *a, **k: (launched, ""))
    monkeypatch.setattr(rs.adb_helper, "shell", lambda *a, **k: "test VPN sink established")
    result = rs.step_start_test_vpn({"serial": "emulator-fake", "vpn_mode": "covering"})
    assert result["established"] is True
    assert result["am_output"] == launched


@pytest.mark.parametrize(
    "output",
    [
        "Permission Denial: starting Intent ... not exported from uid 10154",
        "Error: Activity class {x/.Y} does not exist.",
        "java.lang.SecurityException: ...",
    ],
)
def test_am_start_refusal_markers(output):
    assert rs._am_start_refused(output)


def test_a_plain_launch_is_not_a_refusal():
    assert not rs._am_start_refused("Starting: Intent { cmp=a/.B }")
