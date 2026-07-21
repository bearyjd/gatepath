"""Tests verifying GTK-free import isolation and --help exit behaviour."""

from __future__ import annotations

import subprocess
import sys

import pytest


class TestHelpExitsZero:
    def test_help_exits_zero_without_gtk(self) -> None:
        """python -m gatepath --help must exit 0 even without PyGObject."""
        result = subprocess.run(
            [sys.executable, "-m", "gatepath", "--help"],
            capture_output=True,
            timeout=10,
            env=_env_with_desktop_on_path(),
        )
        assert result.returncode == 0, (
            f"--help returned non-zero.\n"
            f"stdout: {result.stdout.decode()}\n"
            f"stderr: {result.stderr.decode()}"
        )
        # Sanity check: help text mentions gatepath.
        assert b"gatepath" in result.stdout.lower() or b"gatepath" in result.stderr.lower()

    def test_version_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "gatepath", "--version"],
            capture_output=True,
            timeout=10,
            env=_env_with_desktop_on_path(),
        )
        assert result.returncode == 0


class TestPureStdlibModulesDoNotImportGi:
    """Importing pure-stdlib modules must not load gi.repository.*"""

    _PURE_MODULES = [
        "gatepath.portal_probe",
        "gatepath.portal_session",
        "gatepath.blocked_domains",
        "gatepath.audit_log",
        "gatepath.vpn_detector",
    ]

    @pytest.mark.parametrize("module_name", _PURE_MODULES)
    def test_no_gi_import(self, module_name: str) -> None:
        """Import the module in a subprocess and check sys.modules for gi."""
        code = (
            f"import sys; "
            f"import importlib; "
            f"importlib.import_module('{module_name}'); "
            f"gi_mods = [k for k in sys.modules if k == 'gi' or k.startswith('gi.')]; "
            f"print(gi_mods); "
            f"sys.exit(1 if gi_mods else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=10,
            # Add desktop/ to sys.path so `gatepath` is importable.
            env=_env_with_desktop_on_path(),
        )
        assert result.returncode == 0, (
            f"Module {module_name} imported gi: {result.stdout.decode().strip()}"
        )


class TestAppImportableWithoutGi:
    """gatepath.app must import without PyGObject (only run_app() touches gi)."""

    def test_import_app_without_gi(self) -> None:
        code = (
            "import sys, importlib; "
            "importlib.import_module('gatepath.app'); "
            "gi_mods = [k for k in sys.modules if k == 'gi' or k.startswith('gi.')]; "
            "print(gi_mods); "
            "sys.exit(1 if gi_mods else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            timeout=10,
            env=_env_with_desktop_on_path(),
        )
        assert result.returncode == 0, (
            f"gatepath.app imported gi: {result.stdout.decode().strip()}\n"
            f"stderr: {result.stderr.decode()}"
        )


class _FakeWindow:
    """Minimal stand-in for GatepathWindow exposing the launcher's collaborators."""

    def __init__(self) -> None:
        self.opened: list = []
        self._active = False

    def open_portal(self, portal_url: str, active_session: object) -> None:
        self.opened.append((portal_url, active_session))
        self._active = True

    def is_session_active(self) -> bool:
        return self._active


class TestStartPortalMonitoring:
    """Unit tests for the app.py wiring seam (no GTK, injected start)."""

    def test_builds_launcher_and_starts_monitor(self) -> None:
        from gatepath.app import _start_portal_monitoring

        recorded: dict = {}
        sentinel_monitor = object()

        def fake_start(on_detected, *, probe_url):
            recorded["on_detected"] = on_detected
            recorded["probe_url"] = probe_url
            return sentinel_monitor

        win = _FakeWindow()
        monitor = _start_portal_monitoring(win, "http://probe.test", start=fake_start)

        assert monitor is sentinel_monitor
        assert recorded["probe_url"] == "http://probe.test"
        assert callable(recorded["on_detected"])

    def test_detection_callback_drives_open_portal(self) -> None:
        from gatepath.app import _start_portal_monitoring

        captured: dict = {}

        def fake_start(on_detected, *, probe_url):
            captured["on_detected"] = on_detected
            return object()

        win = _FakeWindow()
        # Inject a synchronous launcher (dispatch runs the callback inline, VPN
        # detection stubbed) so the wired callback reaches window.open_portal
        # without a GTK main loop.
        from gatepath.portal_launcher import PortalLauncher

        def launcher_factory(*, open_portal, is_session_active):
            return PortalLauncher(
                open_portal,
                is_session_active,
                detect_vpn=lambda: [],
                dispatch=lambda cb: cb(),
            )

        _start_portal_monitoring(
            win,
            None,
            start=fake_start,
            launcher_factory=launcher_factory,
        )

        captured["on_detected"]("http://portal.test/login")
        assert len(win.opened) == 1
        assert win.opened[0][0] == "http://portal.test/login"

        # Re-entrancy: a second detection while the session is active is dropped.
        captured["on_detected"]("http://portal.test/login")
        assert len(win.opened) == 1


def _env_with_desktop_on_path() -> dict:
    """Return os.environ with desktop/ prepended to PYTHONPATH."""
    import os
    from pathlib import Path

    desktop = str(Path(__file__).resolve().parent.parent)
    repo_root = str(Path(__file__).resolve().parent.parent.parent)
    existing = os.environ.get("PYTHONPATH", "")
    parts = [desktop, repo_root] + ([existing] if existing else [])
    env = os.environ.copy()
    env["PYTHONPATH"] = ":".join(parts)
    return env
