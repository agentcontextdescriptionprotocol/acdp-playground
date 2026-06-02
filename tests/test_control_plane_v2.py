"""Tests for ControlPlaneClient V2: tenant + event-id forwarding, admin
endpoints, and Retry-After cooperative retry."""

from __future__ import annotations


import httpx

from playground.config import Settings
from playground.control_plane import ControlPlaneClient


def _cp(handler, **overrides) -> ControlPlaneClient:
    params = dict(
        control_plane_url="http://cp.test",
        control_plane_hmac_secret="cp-secret",
        control_plane_admin_token="admin-tok",
    )
    params.update(overrides)
    settings = Settings(**params)
    cp = ControlPlaneClient(settings)
    cp._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return cp


async def test_forward_webhook_propagates_tenant_and_event_id():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        return httpx.Response(204)

    cp = _cp(handler)
    await cp.forward_webhook(
        b'{"type":"context_published"}',
        headers={
            "X-ACDP-Event": "context_published",
            "X-ACDP-Event-Id": "evt-123",
            "X-Run-Id": "run-9",
        },
        tenant_id="tenant-a",
    )
    h = captured["headers"]
    assert captured["path"] == "/ingest/acdp"
    assert h["x-acdp-event"] == "context_published"
    assert h["x-acdp-event-id"] == "evt-123"
    assert h["x-run-id"] == "run-9"
    assert h["x-tenant-id"] == "tenant-a"
    assert h["x-acdp-signature"].startswith("sha256=")
    await cp.aclose()


async def test_forward_webhook_inbound_tenant_header_used_when_no_arg():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["tenant"] = request.headers.get("x-tenant-id")
        return httpx.Response(204)

    cp = _cp(handler)
    await cp.forward_webhook(
        b"{}", headers={"X-Tenant-Id": "tenant-from-header"}
    )
    assert captured["tenant"] == "tenant-from-header"
    await cp.aclose()


async def test_run_notifications_carry_tenant_header():
    captured: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.url.path, request.headers.get("x-tenant-id")))
        return httpx.Response(200, json={"ok": True})

    cp = _cp(handler)
    await cp.notify_run_started("r1", "s1", {}, tenant_id="tenant-a")
    await cp.notify_run_complete("r1", "complete", {}, tenant_id="tenant-a")
    assert captured == [
        ("/runs/started", "tenant-a"),
        ("/runs/r1/complete", "tenant-a"),
    ]
    await cp.aclose()


async def test_introspect_requires_admin_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"active": True, "sub": "did:x"})

    cp = _cp(handler)
    out = await cp.introspect("some-token")
    assert out == {"active": True, "sub": "did:x"}
    await cp.aclose()

    # No admin token → no call, returns None.
    cp2 = _cp(handler, control_plane_admin_token="")
    assert await cp2.introspect("some-token") is None
    await cp2.aclose()


async def test_revocations_feed_query():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["since"] = request.url.params.get("since")
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"entries": [], "next_cursor": None})

    cp = _cp(handler)
    out = await cp.revocations(since_ms=42, limit=10)
    assert out == {"entries": [], "next_cursor": None}
    assert captured["since"] == "42"
    assert captured["auth"] == "Bearer admin-tok"
    await cp.aclose()


async def test_reload_pinned_keys():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/admin/pinned-keys/reload"
        return httpx.Response(200, json={"ok": True, "count": 3})

    cp = _cp(handler)
    out = await cp.reload_pinned_keys()
    assert out == {"ok": True, "count": 3}
    await cp.aclose()


async def test_retry_after_triggers_one_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(200, json={"ok": True})

    cp = _cp(handler)
    await cp.notify_run_started("r1", "s1", {})
    assert calls["n"] == 2  # retried once after Retry-After: 0
    await cp.aclose()


async def test_disabled_when_url_unset():
    settings = Settings(control_plane_url="")
    cp = ControlPlaneClient(settings)
    assert cp.enabled is False
    # No-ops without raising.
    await cp.notify_run_started("r", "s", {})
    assert await cp.introspect("t") is None
