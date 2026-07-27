"""Tests for the desktop portal load-failure model.

The property under protection: a failed portal load must always produce a
visible, specific explanation, and a load *we* cancelled must never be reported
as a failure. Both regress silently rather than loudly, which is why they're
pinned here.

Counterpart to Android's `PortalLoadErrorTest`. The two apps share no code, so
these are independent tests of an independently-written model.
"""

from __future__ import annotations

import pytest

from gatepath import portal_load_error as ple
from gatepath.portal_load_error import PortalLoadError, PortalLoadErrorKind


def _error(
    kind: PortalLoadErrorKind, host: str = "portal.airport.net"
) -> PortalLoadError:
    return PortalLoadError(kind=kind, host=host, technical_detail="WebKitNetworkError:300")


class TestDeliberateCancel:
    """`decision.ignore()` on an off-domain nav must not look like a failure."""

    def test_policy_interruption_is_a_deliberate_cancel(self) -> None:
        # This is what WebKit reports after _on_decide_policy calls ignore().
        assert ple.is_deliberate_cancel("WebKitPolicyError", 202)

    def test_network_cancelled_is_a_deliberate_cancel(self) -> None:
        assert ple.is_deliberate_cancel("WebKitNetworkError", 302)

    def test_real_failures_are_not_cancels(self) -> None:
        # A transport failure must NOT be swallowed as "we meant that".
        assert not ple.is_deliberate_cancel("WebKitNetworkError", 300)
        assert not ple.is_deliberate_cancel("WebKitNetworkError", 199)
        assert not ple.is_deliberate_cancel("WebKitPolicyError", 201)
        assert not ple.is_deliberate_cancel("", 0)

    def test_cancel_codes_are_domain_scoped(self) -> None:
        # 202 means something else outside the policy domain; don't cross-match.
        assert not ple.is_deliberate_cancel("WebKitNetworkError", 202)
        assert not ple.is_deliberate_cancel("WebKitPolicyError", 302)


class TestClassify:
    def test_transport_and_generic_failures_are_unreachable(self) -> None:
        assert ple.classify("WebKitNetworkError", 300) is PortalLoadErrorKind.UNREACHABLE
        assert ple.classify("WebKitNetworkError", 199) is PortalLoadErrorKind.UNREACHABLE

    def test_protocol_and_missing_file_are_unsupported(self) -> None:
        assert ple.classify("WebKitNetworkError", 301) is PortalLoadErrorKind.UNSUPPORTED
        assert ple.classify("WebKitNetworkError", 303) is PortalLoadErrorKind.UNSUPPORTED

    def test_policy_domain_is_unsupported(self) -> None:
        assert ple.classify("WebKitPolicyError", 200) is PortalLoadErrorKind.UNSUPPORTED

    @pytest.mark.parametrize(
        "domain,code",
        [
            ("WebKitNetworkError", 99999),
            ("WebKitDownloadError", 300),
            ("SomeFutureWebKitError", 1),
            ("", 0),
            ("WebKitNetworkError", -1),
        ],
    )
    def test_unknown_inputs_fall_back_without_raising(self, domain: str, code: int) -> None:
        # An unrecognised code must still yield a visible explanation; raising
        # here would put us back to a silent failure.
        assert ple.classify(domain, code) is PortalLoadErrorKind.UNKNOWN


class TestCopy:
    def test_every_kind_has_a_non_empty_title_and_body(self) -> None:
        for kind in PortalLoadErrorKind:
            assert ple.title(kind).strip(), f"{kind} has an empty title"
            assert ple.body(_error(kind)).strip(), f"{kind} has an empty body"

    def test_body_names_the_host_when_known(self) -> None:
        body = ple.body(_error(PortalLoadErrorKind.UNREACHABLE, host="wifi.hotel.example"))
        assert "wifi.hotel.example" in body

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_host_degrades_to_a_readable_phrase(self, blank: str) -> None:
        for kind in PortalLoadErrorKind:
            body = ple.body(_error(kind, host=blank))
            assert "this network's sign-in page" in body
            assert "  " not in body, f"{kind} body has a double space from the blank host"

    def test_body_never_contains_a_full_url(self) -> None:
        # The model takes a host precisely so query-string tokens and MAC
        # addresses can't reach the screen.
        body = ple.body(_error(PortalLoadErrorKind.UNKNOWN, host="gw.example.net"))
        assert "http://" not in body
        assert "https://" not in body
        assert "?" not in body


class TestRetryPolicy:
    def test_cert_rejection_is_not_retryable(self) -> None:
        assert not ple.is_retryable(PortalLoadErrorKind.CERT_REJECTED)

    def test_everything_else_is_retryable(self) -> None:
        for kind in PortalLoadErrorKind:
            if kind is PortalLoadErrorKind.CERT_REJECTED:
                continue
            assert ple.is_retryable(kind), f"{kind} should be retryable"

    def test_cert_rejection_copy_warns_rather_than_reassures(self) -> None:
        body = ple.body(_error(PortalLoadErrorKind.CERT_REJECTED))
        assert "Avoid signing in" in body
