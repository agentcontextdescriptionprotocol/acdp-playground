"""Tests for playground.retry_after — RFC 9110 Retry-After parsing."""

from __future__ import annotations

from playground.retry_after import parse_retry_after

# A fixed "now": 2026-06-01T00:00:00Z.
NOW = 1780272000.0


def test_none_and_empty():
    assert parse_retry_after(None, now=NOW) is None
    assert parse_retry_after("", now=NOW) is None
    assert parse_retry_after("   ", now=NOW) is None


def test_delta_seconds():
    assert parse_retry_after("30", now=NOW) == 30.0
    assert parse_retry_after("0", now=NOW) == 0.0


def test_http_date_future():
    # 60 seconds after NOW.
    delay = parse_retry_after("Mon, 01 Jun 2026 00:01:00 GMT", now=NOW)
    assert delay is not None
    assert abs(delay - 60.0) < 1.0


def test_http_date_past_clamps_to_zero():
    delay = parse_retry_after("Sun, 31 May 2026 00:00:00 GMT", now=NOW)
    assert delay == 0.0


def test_unparseable():
    assert parse_retry_after("not-a-date", now=NOW) is None
    assert parse_retry_after("3.5", now=NOW) is None  # not all-digits
