"""Tests for RFC-ACDP-0007 §4/§5 error-envelope parsing."""

from __future__ import annotations

import httpx
import pytest

from acdp_client.client import AcdpClient, AcdpHTTPError, SupersededError
from acdp_client.models import CursorError, parse_error_envelope


def test_parse_error_envelope_nested():
    code, msg, details = parse_error_envelope(
        {"error": {"code": "superseded_target", "message": "nope",
                   "details": {"reason": "not_found"}}}
    )
    assert code == "superseded_target"
    assert msg == "nope"
    assert details == {"reason": "not_found"}


def test_parse_error_envelope_flat_fallback():
    code, msg, details = parse_error_envelope({"code": "rate_limited", "detail": "slow down"})
    assert code == "rate_limited"
    assert msg == "slow down"
    assert details is None


def test_parse_error_envelope_garbage():
    assert parse_error_envelope("not-json") == (None, "", None)
    assert parse_error_envelope({}) == (None, "", None)


def _client(handler) -> AcdpClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return AcdpClient("http://reg.test", http=http)


async def test_publish_raises_superseded_error_with_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            headers={"content-type": "application/acdp+json"},
            json={"error": {"code": "superseded_target", "message": "denied",
                            "details": {"reason": "not_found"}}},
        )

    client = _client(handler)
    with pytest.raises(SupersededError) as e:
        await client.publish('{"x":1}')
    assert e.value.code == "superseded_target"
    assert e.value.reason == "not_found"
    assert e.value.status == 400
    await client.aclose()


async def test_cross_registry_supersession_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": "superseded_target",
                            "details": {"reason": "cross_registry_supersession_unsupported"}}},
        )

    client = _client(handler)
    with pytest.raises(SupersededError) as e:
        await client.publish('{"x":1}')
    assert e.value.reason == "cross_registry_supersession_unsupported"
    await client.aclose()


async def test_generic_error_exposes_code():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "not_authorized", "message": "no"}})

    client = _client(handler)
    with pytest.raises(AcdpHTTPError) as e:
        await client.publish('{"x":1}')
    assert not isinstance(e.value, SupersededError)
    assert e.value.code == "not_authorized"
    assert e.value.status == 403
    await client.aclose()


async def test_search_surfaces_cursor_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": "cursor_expired", "message": "stale"}}
        )

    client = _client(handler)
    with pytest.raises(CursorError) as e:
        await client.search("q", cursor="old")
    assert e.value.code == "cursor_expired"
    await client.aclose()


async def test_accept_header_advertises_acdp_media_type():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["accept"] = request.headers.get("accept")
        return httpx.Response(200, json={"matches": [], "next_cursor": None})

    client = _client(handler)
    await client.search("q")
    assert "application/acdp+json" in seen["accept"]
    await client.aclose()
