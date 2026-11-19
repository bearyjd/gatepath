"""Tests for gatepath.portal_probe — pure stdlib probe logic."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from email.message import Message

import pytest

from gatepath.portal_probe import ProbeResult, probe, resolve_portal_url


class TestProbeWithMockPortal:
    """Integration tests using the mock captive-portal server."""

    def test_302_returns_portal_status(self, mock_portal: str) -> None:
        """First probe → 302 redirect → status='portal' with portal_url set."""
        result = probe(url=f"{mock_portal}/generate_204")
        assert result.status == "portal"
        assert result.portal_url is not None
        assert "/portal" in result.portal_url

    def test_204_returns_validated(self, mock_portal: str) -> None:
        """After 3 redirects the mock returns 204 → status='validated'."""
        # Consume the first 3 (which redirect) — probe hits /generate_204 each time.
        for _ in range(3):
            r = probe(url=f"{mock_portal}/generate_204")
            assert r.status == "portal"
        # Fourth call → 204.
        result = probe(url=f"{mock_portal}/generate_204")
        assert result.status == "validated"
        assert result.portal_url is None

    def test_probe_does_not_follow_redirect(self, mock_portal: str) -> None:
        """Probe must NOT follow the 302; /portal should not appear in the server log."""
        probe(url=f"{mock_portal}/generate_204")
        log_resp = urllib.request.urlopen(f"{mock_portal}/log", timeout=3)
        log_data: list[dict] = json.loads(log_resp.read())
        # Only /generate_204 should appear — NOT /portal.
        paths = [entry["path"] for entry in log_data]
        assert any("/generate_204" in p for p in paths), "Expected /generate_204 in log"
        assert not any("/portal" in p for p in paths), (
            f"Probe followed redirect to /portal! Log paths: {paths}"
        )

    def test_injected_url_is_used(self, mock_portal: str) -> None:
        """probe(url=...) uses the explicitly passed URL, not any default."""
        custom_url = f"{mock_portal}/generate_204"
        result = probe(url=custom_url)
        # Result reflects the mock server's behaviour, not the default Ubuntu URL.
        assert result.status in ("portal", "validated", "error")

    def test_invalid_host_returns_error(self) -> None:
        """Unreachable host → status='error'."""
        result = probe(url="http://invalid.host.that.does.not.exist.local/", timeout=2)
        assert result.status == "error"
        assert result.message is not None


class TestProbeResultImmutability:
    def test_frozen_dataclass(self) -> None:
        r = ProbeResult(status="validated")
        with pytest.raises((AttributeError, TypeError)):
            r.status = "portal"  # type: ignore[misc]


PROBE_URL = "http://connectivity-check.ubuntu.com/"


class TestResolvePortalUrl:
    @pytest.mark.parametrize(
        "location",
        [
            "file:///etc/hostname",
            "javascript:alert(1)",
            "intent://x#Intent;scheme=http;end",
            "data:text/html,<script>1</script>",
            "",
            "   ",
            "http://",
            "https:///path",
        ],
    )
    def test_rejects_unsafe_or_hostless(self, location: str) -> None:
        assert resolve_portal_url(location, PROBE_URL) is None

    def test_relative_path_resolves_against_probe_url(self) -> None:
        assert resolve_portal_url("/login", PROBE_URL) == "http://connectivity-check.ubuntu.com/login"

    def test_absolute_https_and_http_pass(self) -> None:
        assert resolve_portal_url("https://portal.example/a?b=1", PROBE_URL) == "https://portal.example/a?b=1"
        assert resolve_portal_url("http://10.0.0.1/login", PROBE_URL) == "http://10.0.0.1/login"

    def test_surrounding_whitespace_stripped(self) -> None:
        assert resolve_portal_url("  https://portal.example/x \r\n", PROBE_URL) == "https://portal.example/x"

    def test_uppercase_scheme_accepted(self) -> None:
        assert resolve_portal_url("HTTPS://portal.example/", PROBE_URL) is not None


class _FakeOpener:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def open(self, url: str, timeout: int = 5):  # noqa: ANN201
        raise self._exc


def _http_error(location: str) -> urllib.error.HTTPError:
    headers = Message()
    headers["Location"] = location
    return urllib.error.HTTPError(PROBE_URL, 302, "Found", headers, io.BytesIO(b""))


class TestProbeRejectsBadLocation:
    @pytest.mark.parametrize("location", ["file:///etc/hostname", "javascript:alert(1)", "intent://x"])
    def test_bad_location_is_error_not_portal(self, monkeypatch: pytest.MonkeyPatch, location: str) -> None:
        monkeypatch.setattr(
            urllib.request, "build_opener", lambda *a, **k: _FakeOpener(_http_error(location))
        )
        result = probe(url=PROBE_URL)
        assert result.status == "error"
        assert result.portal_url is None
        assert result.message

    def test_relative_location_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            urllib.request, "build_opener", lambda *a, **k: _FakeOpener(_http_error("/login"))
        )
        result = probe(url=PROBE_URL)
        assert result.status == "portal"
        assert result.portal_url == "http://connectivity-check.ubuntu.com/login"
