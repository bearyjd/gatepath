"""Tests for gatepath.portal_probe — pure stdlib probe logic."""

from __future__ import annotations

import json
import urllib.request

import pytest

from gatepath.portal_probe import ProbeResult, probe


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
