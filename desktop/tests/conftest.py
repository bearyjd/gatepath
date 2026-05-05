"""Pytest configuration for desktop tests.

Provides:
  - sys.path setup so `gatepath` and `mockportal` are importable.
  - `mock_portal` fixture: starts the mock captive-portal server on a free
    port, yields the base URL, resets state between tests, shuts down after.
"""

from __future__ import annotations

import socket
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Generator

import pytest

# Ensure repo root is on sys.path so both `gatepath` and `mockportal` resolve.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Also ensure the desktop/ directory itself is on sys.path for `gatepath`.
_DESKTOP = Path(__file__).resolve().parent.parent
if str(_DESKTOP) not in sys.path:
    sys.path.insert(0, str(_DESKTOP))

from mockportal.server import build_server  # noqa: E402


def _free_port() -> int:
    """Find an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def mock_portal() -> Generator[str, None, None]:
    """Start mock captive portal; yield base URL; reset between tests; shutdown after."""
    port = _free_port()
    server, state = build_server(host="127.0.0.1", port=port, complete_after=3)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"

    yield base_url

    # Reset state (also done before each test via the fixture being function-scoped).
    try:
        urllib.request.urlopen(
            urllib.request.Request(f"{base_url}/reset", method="POST"),
            timeout=2,
        )
    except Exception:  # noqa: BLE001
        pass

    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
