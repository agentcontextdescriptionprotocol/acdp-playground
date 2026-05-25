"""Offline smoke test.

Exercises the wiring without a running registry or LLM. Verifies:
- All scenario modules import + register cleanly
- AcdpProducer + AcdpVerifier round-trip a publish request
- BasePlaygroundAgent.publish path works against a fake AcdpClient
- The webhook signature path validates a payload that we hand-sign

Run:
    uv run python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

# Make sure we run from the repo root regardless of CWD.
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


async def main() -> int:
    print("== ACDP playground smoke test ==")
    failures = 0

    failures += await _check_scenarios_load()
    failures += await _check_sdk_round_trip()
    failures += await _check_agent_publish_path()
    failures += await _check_webhook_signature()

    print()
    if failures:
        print(f"FAIL: {failures} check(s) failed")
        return 1
    print("PASS: all smoke checks passed")
    return 0


# ── checks ───────────────────────────────────────────────────────────────


async def _check_scenarios_load() -> int:
    print("\n[1/4] scenario catalog loads")
    try:
        from playground.scenarios import list_scenarios
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL import: {e}")
        return 1

    scenarios = list_scenarios()
    expected = {
        "s1_single_publish", "s2_producer_consumer", "s3_fanout",
        "s4_chain", "s5_cross_registry", "s6_restricted",
        "s7_supersession", "s8_cross_org",
    }
    got = {s.id for s in scenarios}
    missing = expected - got
    extras = got - expected
    print(f"  loaded: {sorted(got)}")
    if missing:
        print(f"  MISSING: {sorted(missing)}")
    if extras:
        print(f"  unexpected: {sorted(extras)}")
    return 1 if missing else 0


async def _check_sdk_round_trip() -> int:
    print("\n[2/4] acdp-py SDK round-trip")
    try:
        from acdp import AcdpProducer, AcdpVerifier
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL import: {e}")
        return 1

    seed = bytes(range(32))
    producer = AcdpProducer.from_seed(
        seed,
        "did:web:registry-a.playground.local:agents:smoke",
        "did:web:registry-a.playground.local:agents:smoke#key-1",
    )

    raw = producer.build_publish_request(
        title="Smoke test",
        context_type="data_snapshot",
        visibility="public",
        summary="hello",
        metadata=json.dumps({"k": "v"}),
    )
    req = json.loads(raw)
    body = {k: v for k, v in req.items() if k != "content_hash"}

    # Hash + signature verify against the SDK verifier
    try:
        AcdpVerifier.verify_content_hash(json.dumps(body), req["content_hash"])
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL content_hash: {e}")
        return 1
    try:
        AcdpVerifier.verify_signature(
            producer.public_key_b64,
            body["signature"]["value"],
            req["content_hash"],
        )
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL signature: {e}")
        return 1

    print(f"  ok: agent_did={producer.agent_did}")
    print(f"  ok: content_hash={req['content_hash'][:32]}...")
    return 0


async def _check_agent_publish_path() -> int:
    print("\n[3/4] BasePlaygroundAgent.publish against fake registry")
    try:
        from acdp import AcdpProducer
        from playground.agents.base import AgentTask, BasePlaygroundAgent
        from acdp_client.models import PublishResponse
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL import: {e}")
        return 1

    captured: dict[str, Any] = {}

    class FakeClient:
        async def publish(self, request_json: str, *, idempotency_key=None):
            captured["request"] = json.loads(request_json)
            return PublishResponse(
                ctx_id="acdp://registry-a.playground.local/00000000-0000-4000-8000-000000000001",
                lineage_id="lin:sha256:abc",
                version=1,
                created_at=datetime.now(timezone.utc),
                status="active",
            )

        async def resolve(self, ctx_id, authority_map):  # not used
            raise NotImplementedError

    class StubAgent(BasePlaygroundAgent):
        framework = "stub"

        async def call_llm(self, prompt: str) -> str:
            return f"stub: {prompt[:40]}"

    seed = bytes(range(1, 33))
    producer = AcdpProducer.from_seed(
        seed,
        "did:web:registry-a.playground.local:agents:stub",
        "did:web:registry-a.playground.local:agents:stub#key-1",
    )
    queue: asyncio.Queue = asyncio.Queue()
    agent = StubAgent(producer, FakeClient(), queue, "run-smoke", slug="stub")  # type: ignore[arg-type]

    out = await agent.run(AgentTask(prompt="hello", title="t", override_response="canned"))

    if out.llm_response != "canned":
        print(f"  FAIL override_response: {out.llm_response!r}")
        return 1
    if not captured.get("request"):
        print("  FAIL: client.publish was not called")
        return 1
    if captured["request"]["title"] != "t":
        print(f"  FAIL: title mismatch: {captured['request']['title']!r}")
        return 1

    events: list[str] = []
    while not queue.empty():
        events.append(queue.get_nowait().type)
    print(f"  ok: emitted events={events}")
    print(f"  ok: ctx_id={out.ctx_id}")
    return 0


async def _check_webhook_signature() -> int:
    print("\n[4/4] webhook signature verify")
    secret = "test-secret"
    body = b'{"type":"context_published","agent_id":"did:web:x","ctx_id":"acdp://r/1"}'
    expected = f"sha256={hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}"

    try:
        from playground.api.webhooks import _verify
        _verify(secret, body, expected)
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL valid signature was rejected: {e}")
        return 1

    try:
        from fastapi import HTTPException
        from playground.api.webhooks import _verify as _verify2
        try:
            _verify2(secret, body, "sha256=deadbeef")
        except HTTPException:
            pass
        else:
            print("  FAIL: bad signature was accepted")
            return 1
    except Exception as e:  # noqa: BLE001
        print(f"  FAIL: {e}")
        return 1

    print("  ok: valid signature accepted, bad signature rejected")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
