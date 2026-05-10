"""Tests for :py:mod:`gatepath.portal_webview_runner`.

Only exercises the parsing path. The :py:func:`run_window` path requires
GTK + WebKit and is integration-tested in PR-C.
"""

from __future__ import annotations

import pytest

from gatepath.portal_webview_runner import parse_argv


def test_parse_argv_accepts_http_url() -> None:
    assert parse_argv(["runner", "http://captive.example/login"]) == "http://captive.example/login"


def test_parse_argv_accepts_https_url() -> None:
    assert parse_argv(["runner", "https://captive.example/"]) == "https://captive.example/"


def test_parse_argv_accepts_ip_literal_with_port() -> None:
    assert (
        parse_argv(["runner", "http://192.0.2.1:8080/captive"])
        == "http://192.0.2.1:8080/captive"
    )


def test_parse_argv_rejects_missing_url() -> None:
    assert parse_argv(["runner"]) is None


def test_parse_argv_rejects_extra_args() -> None:
    assert parse_argv(["runner", "http://example.com/", "extra"]) is None


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "ftp://example.com/",
        "data:text/plain,hi",
    ],
)
def test_parse_argv_rejects_disallowed_schemes(url: str) -> None:
    assert parse_argv(["runner", url]) is None


def test_parse_argv_rejects_url_with_no_netloc() -> None:
    # Bare scheme with empty netloc shouldn't slip through.
    assert parse_argv(["runner", "http:///nowhere"]) is None


def test_parse_argv_rejects_embedded_newline() -> None:
    assert parse_argv(["runner", "http://example.com/\nx"]) is None


def test_parse_argv_rejects_embedded_null() -> None:
    assert parse_argv(["runner", "http://example.com/\0x"]) is None


def test_parse_argv_rejects_oversized_url() -> None:
    huge = "http://example.com/" + "a" * 5000
    assert parse_argv(["runner", huge]) is None


def test_parse_argv_rejects_malformed_url() -> None:
    assert parse_argv(["runner", "not a url at all"]) is None
