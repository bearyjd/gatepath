"""Tests for the mock captive portal server itself.

These must pass before either platform is allowed to depend on the server.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from collections.abc import Iterator
from urllib.error import HTTPError

import pytest

from mockportal.server import build_server


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def portal() -> Iterator[str]:
    port = _free_port()
    server, _ = build_server(host="127.0.0.1", port=port, complete_after=3)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    # Brief wait for socket to be ready.
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{base}/log", timeout=1).read()
            break
        except OSError:
            time.sleep(0.02)
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _open(url: str, *, method: str = "GET", data: bytes | None = None):
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, data=data, method=method)
    try:
        return opener.open(req, timeout=2)
    except HTTPError as e:
        return e


def test_generate_204_redirects_first_then_validates(portal: str) -> None:
    for _ in range(3):
        resp = _open(f"{portal}/generate_204")
        assert resp.status == 302
        assert resp.headers["Location"] == f"{portal}/portal"
    resp = _open(f"{portal}/generate_204")
    assert resp.status == 204


def test_portal_page_contains_login_form(portal: str) -> None:
    resp = _open(f"{portal}/portal")
    assert resp.status == 200
    body = resp.read().decode("utf-8")
    assert "<form" in body and 'action="/login"' in body
    assert "evil-tracker.example.com" in body
    assert "external-site.example.com" in body


def test_login_redirects_to_probe(portal: str) -> None:
    resp = _open(f"{portal}/login", method="POST", data=b"user=tester")
    assert resp.status == 302
    assert resp.headers["Location"] == f"{portal}/generate_204"


def test_login_authenticates_so_probe_validates(portal: str) -> None:
    # Before login the probe is captive (302); after a successful /login the
    # session is authenticated and every probe validates (204), regardless of
    # the redirect counter. Models the real "captive until you sign in" flow.
    assert _open(f"{portal}/generate_204").status == 302
    _open(f"{portal}/login", method="POST", data=b"user=tester")
    assert _open(f"{portal}/generate_204").status == 204
    assert _open(f"{portal}/generate_204").status == 204


def test_reset_clears_authentication(portal: str) -> None:
    _open(f"{portal}/login", method="POST", data=b"user=tester")
    assert _open(f"{portal}/generate_204").status == 204
    _open(f"{portal}/reset", method="POST", data=b"")
    assert _open(f"{portal}/generate_204").status == 302


def test_reset_resets_counter(portal: str) -> None:
    for _ in range(3):
        _open(f"{portal}/generate_204")
    _open(f"{portal}/reset", method="POST", data=b"")
    resp = _open(f"{portal}/generate_204")
    assert resp.status == 302


def test_log_returns_recorded_requests(portal: str) -> None:
    _open(f"{portal}/generate_204")
    resp = _open(f"{portal}/log")
    assert resp.status == 200
    entries = json.loads(resp.read().decode("utf-8"))
    assert any(e["path"].startswith("/generate_204") for e in entries)


def test_unknown_path_404(portal: str) -> None:
    resp = _open(f"{portal}/does-not-exist")
    assert resp.status == 404


def test_portal_host_env_overrides_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # The module-level PORTAL_HOST default is read from the environment at import
    # time, so harnesses (e.g. mac80211_hwsim) can bind a routable AP IP. Reload
    # the module under a patched env and restore the default afterwards so other
    # tests keep the loopback binding.
    import importlib

    import mockportal.server as srv

    monkeypatch.setenv("PORTAL_HOST", "192.0.2.7")
    try:
        reloaded = importlib.reload(srv)
        assert reloaded.PORTAL_HOST == "192.0.2.7"
    finally:
        monkeypatch.delenv("PORTAL_HOST", raising=False)
        importlib.reload(srv)
        assert srv.PORTAL_HOST == "127.0.0.1"
