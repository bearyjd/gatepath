"""Tests for gatepath.http_fetcher — pure stdlib, no-follow HTTP GET."""

from __future__ import annotations

import time

from gatepath import http_fetcher


def test_redirect_is_reported_not_followed(mock_portal: str) -> None:
    result = http_fetcher.fetch(f"{mock_portal}/generate_204")
    assert result.status_code == 302
    assert result.location.endswith("/portal")
    assert result.error is None


def test_date_header_is_parsed(mock_portal: str) -> None:
    result = http_fetcher.fetch(f"{mock_portal}/generate_204")
    assert result.date_epoch_seconds is not None
    assert abs(result.date_epoch_seconds - time.time()) < 60


def test_body_is_captured_for_a_page(mock_portal: str) -> None:
    result = http_fetcher.fetch(f"{mock_portal}/portal")
    assert result.status_code == 200
    assert "Test Portal" in result.body


def test_redirect_loop_endpoints_are_reported_individually(mock_portal: str) -> None:
    # PR 2 added these to the shared mock for exactly this purpose.
    result = http_fetcher.fetch(f"{mock_portal}/loop-a")
    assert result.status_code == 302
    assert result.location.endswith("/loop-b")


def test_connection_failure_becomes_an_error_never_raises() -> None:
    result = http_fetcher.fetch("http://127.0.0.1:1/nope")
    assert result.status_code is None
    assert result.error is not None
