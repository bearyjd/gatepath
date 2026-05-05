"""Tests for gatepath.blocked_domains."""

from __future__ import annotations

import pytest

from gatepath.blocked_domains import BLOCKED_DOMAINS, is_blocked


class TestIsBlocked:
    def test_exact_match(self) -> None:
        assert is_blocked("google-analytics.com") is True

    def test_subdomain_match(self) -> None:
        assert is_blocked("subdomain.google-analytics.com") is True

    def test_deep_subdomain_match(self) -> None:
        assert is_blocked("a.b.google-analytics.com") is True

    def test_non_match(self) -> None:
        assert is_blocked("example.com") is False

    def test_legit_domain_not_blocked(self) -> None:
        assert is_blocked("google.com") is False

    def test_case_insensitive(self) -> None:
        assert is_blocked("Google-Analytics.COM") is True

    def test_evil_tracker_blocked(self) -> None:
        """The mock portal's tracker domain should be blocked."""
        assert is_blocked("evil-tracker.example.com") is True

    def test_partial_suffix_not_matched(self) -> None:
        """e.g. 'notgoogle-analytics.com' should NOT match 'google-analytics.com'."""
        assert is_blocked("notgoogle-analytics.com") is False

    def test_empty_string_not_blocked(self) -> None:
        assert is_blocked("") is False

    def test_blocked_domains_is_frozenset(self) -> None:
        assert isinstance(BLOCKED_DOMAINS, frozenset)

    def test_doubleclick_blocked(self) -> None:
        assert is_blocked("ad.doubleclick.net") is True

    def test_mixpanel_exact(self) -> None:
        assert is_blocked("mixpanel.com") is True
