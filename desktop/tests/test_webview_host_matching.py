"""Tests for the desktop same-origin host-matching rule.

Deliberately mirrors `android/app/src/test/.../WebViewHostMatchingTest.kt`
case for case. The two apps share no code, so the only thing keeping the rule
identical is that both suites assert the same behaviour — if you change one
side, change the other.

The rule decides whether a navigation is same-origin or off-domain. Off-domain
navigations are counted in the audit log and allowed to load; getting the rule
wrong doesn't break the page, it silently corrupts what the audit log means.
"""

from __future__ import annotations

import pytest

from gatepath.webview_host_matching import is_same_origin_host


@pytest.mark.parametrize(
    "request_host,portal_host",
    [
        ("example.com", "example.com"),
        ("portal.airport.net", "portal.airport.net"),
    ],
)
def test_exact_host_match_is_same_origin(request_host: str, portal_host: str) -> None:
    assert is_same_origin_host(request_host, portal_host)


@pytest.mark.parametrize(
    "request_host,portal_host",
    [
        ("login.example.com", "example.com"),
        ("a.b.example.com", "example.com"),
        ("auth.portal.airport.net", "portal.airport.net"),
    ],
)
def test_subdomain_of_portal_host_is_same_origin(request_host: str, portal_host: str) -> None:
    assert is_same_origin_host(request_host, portal_host)


@pytest.mark.parametrize(
    "request_host,portal_host",
    [
        ("attacker.com", "example.com"),
        ("portal.airport.net", "example.com"),
    ],
)
def test_unrelated_host_is_not_same_origin(request_host: str, portal_host: str) -> None:
    assert not is_same_origin_host(request_host, portal_host)


@pytest.mark.parametrize(
    "request_host",
    ["evil-example.com", "notexample.com", "xexample.com"],
)
def test_lookalike_host_is_not_same_origin(request_host: str) -> None:
    # Classic prefix confusion: the match must be on a dot boundary, not a
    # raw suffix.
    assert not is_same_origin_host(request_host, "example.com")


@pytest.mark.parametrize(
    "request_host,portal_host",
    [
        ("EXAMPLE.com", "example.com"),
        ("Login.Example.COM", "example.com"),
        ("example.com", "EXAMPLE.COM"),
    ],
)
def test_host_comparison_is_case_insensitive(request_host: str, portal_host: str) -> None:
    assert is_same_origin_host(request_host, portal_host)


@pytest.mark.parametrize(
    "request_host,portal_host",
    [
        ("example.com.", "example.com"),
        ("example.com", "example.com."),
        ("login.example.com.", "example.com"),
    ],
)
def test_trailing_dot_fqdn_is_normalized_on_either_side(
    request_host: str, portal_host: str
) -> None:
    assert is_same_origin_host(request_host, portal_host)


@pytest.mark.parametrize("request_host", ["anything.com", "example.com", "example.com.", "."])
def test_blank_portal_host_treats_every_request_as_off_domain(request_host: str) -> None:
    """Defensive: unparseable portal host must not make everything same-origin.

    Also pins the would-have-been bug — `endswith("." + "")` is `endswith(".")`,
    which would match any trailing-dot FQDN.
    """
    assert not is_same_origin_host(request_host, "")


@pytest.mark.parametrize("request_host", ["", "   "])
def test_blank_request_host_is_not_same_origin(request_host: str) -> None:
    assert not is_same_origin_host(request_host, "example.com")


@pytest.mark.parametrize(
    "request_host,portal_host",
    [
        (" example.com ", "example.com"),
        ("example.com", " example.com "),
    ],
)
def test_whitespace_around_hosts_is_tolerated(request_host: str, portal_host: str) -> None:
    assert is_same_origin_host(request_host, portal_host)


def test_port_is_not_part_of_the_host_identity() -> None:
    """Callers pass `urlparse(...).hostname`, which strips the port.

    The previous implementation compared `netloc`, so a portal on :8080
    redirecting to :80 on the same machine read as off-domain.
    """
    from urllib.parse import urlparse

    portal = urlparse("http://192.168.1.1:8080/login").hostname or ""
    nav = urlparse("http://192.168.1.1/grant").hostname or ""
    assert is_same_origin_host(nav, portal)
