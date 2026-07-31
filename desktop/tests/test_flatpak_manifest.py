"""Drift guard for the Flatpak sandbox's D-Bus grants.

Issue #126: the manifest granted `org.freedesktop.NetworkManager` and
`org.freedesktop.resolve1` but not the netns helper's own bus name, so
`NetnsClient.connect()` always raised `HelperUnavailable` inside the sandbox —
even on a host where the helper was installed. Flatpak users could therefore
never reach the confined path, which is the product's core security property.

The grant is a cross-file contract: the manifest string must match the name the
Python client requests, the Rust helper owns, and the D-Bus activation file
declares. Nothing links them at build time, and a typo fails the same way a
missing entry does — silently, at runtime, as a fallback to the unconfined
path. Per CLAUDE.md, contracts like this get a drift guard rather than a
comment (cf. schema-parity.yml and the netns refusal-reason round-trip).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "desktop" / "com.ventouxlabs.Gatepath.yml"
HELPER_DATA = REPO_ROOT / "desktop" / "gatepath-netns-helper" / "data"
DBUS_SERVICE_RS = (
    REPO_ROOT / "desktop" / "gatepath-netns-helper" / "src" / "dbus_service.rs"
)


def _finish_args() -> list[str]:
    return yaml.safe_load(MANIFEST.read_text())["finish-args"]


def _system_talk_names() -> set[str]:
    return {
        a.split("=", 1)[1]
        for a in _finish_args()
        if a.startswith("--system-talk-name=")
    }


def test_manifest_grants_the_netns_helper_bus_name():
    """#126's decision: the Flatpak may reach the helper.

    Without this the sandbox silently falls back to the unconfined path on
    hosts that actually have the helper installed.
    """
    from gatepath.netns_client import BUS_NAME

    assert BUS_NAME in _system_talk_names(), (
        f"{BUS_NAME} is not granted in {MANIFEST.name}; the Flatpak cannot "
        "reach the netns helper and will fall back to the unconfined path"
    )


def test_granted_name_matches_the_name_the_helper_owns():
    """Python client, Rust helper and manifest must name the same bus.

    A typo in any one of them degrades to the unconfined path at runtime with
    no build-time signal.
    """
    from gatepath.netns_client import BUS_NAME

    rust = re.search(
        r'pub const BUS_NAME:\s*&str\s*=\s*"([^"]+)"', DBUS_SERVICE_RS.read_text()
    )
    assert rust, "could not parse BUS_NAME out of dbus_service.rs"
    assert rust.group(1) == BUS_NAME, (
        f"Rust helper owns {rust.group(1)!r} but the Python client requests "
        f"{BUS_NAME!r}"
    )
    assert BUS_NAME in _system_talk_names()


def test_granted_name_matches_the_dbus_activation_file():
    from gatepath.netns_client import BUS_NAME

    service_file = HELPER_DATA / f"{BUS_NAME}.service"
    assert service_file.exists(), (
        f"no D-Bus activation file named for {BUS_NAME}; the manifest grant "
        "would point at a bus name nothing activates"
    )
    assert f"Name={BUS_NAME}" in service_file.read_text()


def test_sandbox_still_forbids_x11_home_and_device_all():
    """#126 widens the sandbox's D-Bus reach; it must not widen anything else.

    The manifest header states these three as explicit spec prohibitions, so
    guard them in the same pass that grants the helper.
    """
    args = _finish_args()
    assert "--socket=x11" not in args
    assert "--filesystem=home" not in args
    assert "--device=all" not in args
