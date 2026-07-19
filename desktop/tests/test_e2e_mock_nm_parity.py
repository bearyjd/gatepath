"""Drift guard: the e2e-docker NM mock must publish the property names the
production consumers actually read.

Why this exists: the NM Device connectivity property was renamed twice in this
repo's history (bare ``Connectivity`` → ``Ip4Connectivity``, once in the Rust
helper — caught by the hwsim harness — and once in the Python monitor,
commit 3509791). Both times ``tests/e2e-docker/client/dbusmock_nm.py`` was
missed, silently breaking the desktop e2e: the mock kept advertising the
legacy name, the production lookup read the real one, and ``nm_lookup``
failed with ``expected wlan0, got None``.

Same philosophy as ``test_netns_client.py``'s refusal-reason round-trip: parse
the sources on both sides of the contract and fail the unit suite — which runs
everywhere — instead of trusting two files in different test layers to stay in
sync by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MOCK_NM = REPO_ROOT / "tests" / "e2e-docker" / "client" / "dbusmock_nm.py"
PORTAL_MONITOR = REPO_ROOT / "desktop" / "gatepath" / "portal_monitor.py"
RUST_NM = (
    REPO_ROOT / "desktop" / "gatepath-netns-helper" / "src" / "network_manager.rs"
)


def _mock_device_property_names() -> set[str]:
    """Quoted keys of the Device-interface property dict in the e2e NM mock."""
    source = MOCK_NM.read_text(encoding="utf-8")
    match = re.search(
        r"AddObject\(\s*DEVICE_PATH,\s*DEVICE_IFACE,\s*\{(?P<body>.*?)\}",
        source,
        re.DOTALL,
    )
    assert match, "could not locate the Device AddObject property dict in dbusmock_nm.py"
    return set(re.findall(r'"([A-Za-z0-9]+)":', match.group("body")))


def test_e2e_mock_publishes_the_property_python_monitor_reads() -> None:
    monitor_source = PORTAL_MONITOR.read_text(encoding="utf-8")
    reads = set(re.findall(r"device\.(Ip[46]Connectivity|Connectivity)\b", monitor_source))
    assert reads, "portal_monitor.py no longer reads a connectivity property?"
    mock_props = _mock_device_property_names()
    missing = reads - mock_props
    assert not missing, (
        f"portal_monitor.py reads {sorted(missing)} but the e2e NM mock "
        f"(dbusmock_nm.py) only publishes {sorted(mock_props)} — the desktop "
        "e2e nm_lookup step will fail with 'expected wlan0, got None'"
    )


def test_e2e_mock_publishes_the_property_rust_helper_reads() -> None:
    rust_source = RUST_NM.read_text(encoding="utf-8")
    reads = set(re.findall(r'name\s*=\s*"(Ip[46]Connectivity|Connectivity)"', rust_source))
    assert reads, "network_manager.rs no longer declares a connectivity zbus property?"
    mock_props = _mock_device_property_names()
    missing = reads - mock_props
    assert not missing, (
        f"network_manager.rs reads {sorted(missing)} but the e2e NM mock "
        f"(dbusmock_nm.py) only publishes {sorted(mock_props)}"
    )


def test_e2e_mock_does_not_advertise_the_legacy_connectivity_name() -> None:
    """Real NM ≥1.16 has no bare ``Connectivity`` on the Device interface.

    Publishing it in the mock would let a consumer regressing to the legacy
    name pass the e2e while failing on real hardware — the exact inversion of
    what a mock is for.
    """
    assert "Connectivity" not in _mock_device_property_names(), (
        "e2e NM mock advertises the legacy bare `Connectivity` Device property"
    )
