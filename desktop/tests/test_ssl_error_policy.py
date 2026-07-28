"""Tests for the desktop certificate-error bypass scope.

Mirrors `android/app/src/test/.../SslErrorPolicyTest.kt` case for case. The two
apps share no code, so the only thing keeping the rule identical is that both
suites assert the same behaviour.

The property under protection: the bypass stops at the portal host. Off-domain
navigation is deliberately allowed (see `webview_host_matching`), so an
unscoped bypass would disable certificate validation for any host a gateway
chooses to redirect to — on precisely the network where an attacker is on-path
by definition.
"""

from __future__ import annotations

import pytest

from gatepath.ssl_error_policy import should_proceed


@pytest.mark.parametrize(
    "error_host,portal_host",
    [
        ("192.168.1.1", "192.168.1.1"),
        ("login.airport.net", "login.airport.net"),
    ],
)
def test_portal_host_itself_proceeds(error_host: str, portal_host: str) -> None:
    assert should_proceed(error_host, portal_host)


def test_subdomain_of_portal_host_proceeds() -> None:
    # Vendors split splash and grant across sub-hosts of the same domain.
    assert should_proceed("auth.portal.airport.net", "portal.airport.net")


@pytest.mark.parametrize(
    "error_host,portal_host",
    [
        ("accounts.google.com", "192.168.1.1"),
        ("bank.example.com", "portal.airport.net"),
    ],
)
def test_unrelated_host_is_refused(error_host: str, portal_host: str) -> None:
    """The point of the scope: a hostile gateway redirecting to an identity
    provider with an untrusted certificate must not be proceeded past."""
    assert not should_proceed(error_host, portal_host)


@pytest.mark.parametrize("error_host", ["evil-airport.net", "notairport.net"])
def test_lookalike_host_is_refused(error_host: str) -> None:
    assert not should_proceed(error_host, "airport.net")


@pytest.mark.parametrize(
    "error_host,portal_host",
    [("", "portal.airport.net"), ("portal.airport.net", ""), ("", "")],
)
def test_unparseable_or_blank_hosts_fail_closed(error_host: str, portal_host: str) -> None:
    """Unknown host must mean "enforce TLS", never "bypass TLS"."""
    assert not should_proceed(error_host, portal_host)


@pytest.mark.parametrize(
    "error_host,portal_host",
    [
        ("Portal.Airport.NET", "portal.airport.net"),
        ("portal.airport.net.", "portal.airport.net"),
    ],
)
def test_host_comparison_is_case_and_trailing_dot_insensitive(
    error_host: str, portal_host: str
) -> None:
    assert should_proceed(error_host, portal_host)
