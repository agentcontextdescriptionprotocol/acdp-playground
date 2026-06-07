"""Tests for the consumer SSRF guard (acdp_client.safe_http).

Classification is delegated to the Rust ``acdp.AcdpSsrfPolicy`` (acdp-py
0.2.0), so the rejection ``reason`` values follow the Rust ``SsrfReason``
taxonomy (``loopback`` / ``private`` / ``imds`` / ``multicast_or_reserved`` /
``non_https`` / ``ip_literal`` / ``invalid_url`` / ``cross_authority``). The
orchestration (DNS mixed-answer loop, fetch transport) and the userinfo guard
stay in Python and keep their own reason tokens.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from acdp_client.safe_http import (
    DataRefHashMismatch,
    SsrfError,
    SsrfPolicy,
    check_url,
    fetch,
    fetch_data_ref,
    ip_is_forbidden,
    same_authority,
    screen_host,
)

PROD = SsrfPolicy.production()


# ── ip_is_forbidden ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "ip,expect_reason",
    [
        ("203.0.113.10", None),
        ("8.8.8.8", None),
        ("127.0.0.1", "loopback"),
        ("10.0.0.1", "private"),
        ("172.16.5.5", "private"),
        ("192.168.1.1", "private"),
        ("169.254.169.254", "imds"),  # link-local / IMDS
        ("169.254.1.1", "imds"),
        ("100.64.0.1", "private"),  # CGNAT
        ("224.0.0.1", "multicast_or_reserved"),
        ("0.0.0.0", "multicast_or_reserved"),  # "this host" / unspecified
        ("fc00::1", "private"),  # ULA
        ("fe80::1", "imds"),  # IPv6 link-local
        ("::1", "loopback"),
        ("::ffff:169.254.169.254", "imds"),  # v4-mapped IMDS
        ("64:ff9b::a9fe:a9fe", "imds"),  # NAT64 -> IMDS
    ],
)
def test_ip_is_forbidden(ip, expect_reason):
    assert ip_is_forbidden(ip, PROD) == expect_reason


def test_test_loopback_policy_allows_loopback_but_not_private():
    pol = SsrfPolicy.allow_test_loopback()
    assert ip_is_forbidden("127.0.0.1", pol) is None
    assert ip_is_forbidden("::1", pol) is None
    assert ip_is_forbidden("10.0.0.1", pol) == "private"
    assert ip_is_forbidden("169.254.169.254", pol) == "imds"


# ── check_url ────────────────────────────────────────────────────────────────


def test_check_url_rejects_http():
    with pytest.raises(SsrfError) as e:
        check_url("http://data.example.com/x", PROD)
    assert e.value.reason == "non_https"


def test_check_url_rejects_userinfo():
    with pytest.raises(SsrfError) as e:
        check_url("https://user:pass@data.example.com/x", PROD)
    assert e.value.reason == "forbidden_userinfo"


def test_check_url_rejects_ip_literal():
    with pytest.raises(SsrfError) as e:
        check_url("https://169.254.169.254/x", PROD)
    assert e.value.reason == "ip_literal"


def test_check_url_allows_https_hostname():
    check_url("https://data.example.com/x", PROD)  # no raise


# ── same_authority ───────────────────────────────────────────────────────────


def test_same_authority_effective_port():
    base = "https://data.example.com/file"
    assert same_authority(base, "https://data.example.com:443/other") is True
    assert same_authority(base, "https://data.example.com:8443/file") is False
    assert same_authority(base, "http://data.example.com/file") is False
    assert same_authority(base, "https://evil.example.com/file") is False


# ── screen_host (mixed-answer rejection) ─────────────────────────────────────


def test_screen_host_mixed_answer_rejected():
    def resolver(host):
        return ["203.0.113.10", "10.0.0.1"]  # one public, one private

    with pytest.raises(SsrfError) as e:
        screen_host("data.attacker.example", PROD, resolver=resolver)
    assert e.value.reason == "private"


def test_screen_host_all_public_ok():
    def resolver(host):
        return ["203.0.113.10", "8.8.8.8"]

    assert screen_host("ok.example", PROD, resolver=resolver) == ["203.0.113.10", "8.8.8.8"]


def test_screen_host_empty_answer_fails():
    with pytest.raises(SsrfError) as e:
        screen_host("nx.example", PROD, resolver=lambda h: [])
    assert e.value.reason == "dns_failure"


# ── fetch (redirects + size) ─────────────────────────────────────────────────


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def test_fetch_blocks_cross_port_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port in (None, 443):
            return httpx.Response(302, headers={"location": "https://data.example.com:8443/x"})
        return httpx.Response(200, content=b"should-not-reach")

    with pytest.raises(SsrfError) as e:
        await fetch(
            "https://data.example.com/x",
            policy=PROD,
            resolver=lambda h: ["203.0.113.10"],
            client=_client(handler),
        )
    assert e.value.reason == "cross_authority_redirect"


async def test_fetch_follows_same_authority_redirect():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x":
            return httpx.Response(302, headers={"location": "https://data.example.com/y"})
        return httpx.Response(200, content=b"final")

    data = await fetch(
        "https://data.example.com/x",
        policy=PROD,
        resolver=lambda h: ["203.0.113.10"],
        client=_client(handler),
    )
    assert data == b"final"


async def test_fetch_enforces_size_cap():
    pol = SsrfPolicy(max_bytes=8)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 100)

    with pytest.raises(SsrfError) as e:
        await fetch(
            "https://data.example.com/x",
            policy=pol,
            resolver=lambda h: ["203.0.113.10"],
            client=_client(handler),
        )
    assert e.value.reason == "response_too_large"


async def test_fetch_screens_before_connecting():
    """A forbidden target must never reach the transport."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"leaked")

    with pytest.raises(SsrfError):
        await fetch(
            "https://imds.attacker.example/x",
            policy=PROD,
            resolver=lambda h: ["169.254.169.254"],
            client=_client(handler),
        )
    assert calls == []  # transport never invoked


# ── fetch_data_ref (content_hash verification) ───────────────────────────────


async def test_fetch_data_ref_verifies_content_hash():
    payload = b"the-data-bytes"
    digest = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    data_ref = {
        "type": "raw_data",
        "location": "https://data.example.com/export.csv",
        "content_hash": f"sha256:{digest}",
    }
    out = await fetch_data_ref(
        data_ref, policy=PROD, resolver=lambda h: ["203.0.113.10"], client=_client(handler)
    )
    assert out == payload


async def test_fetch_data_ref_hash_mismatch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"tampered")

    data_ref = {
        "location": "https://data.example.com/export.csv",
        "content_hash": "sha256:" + "0" * 64,
    }
    with pytest.raises(DataRefHashMismatch):
        await fetch_data_ref(
            data_ref, policy=PROD, resolver=lambda h: ["203.0.113.10"], client=_client(handler)
        )
