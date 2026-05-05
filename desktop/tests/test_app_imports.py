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
