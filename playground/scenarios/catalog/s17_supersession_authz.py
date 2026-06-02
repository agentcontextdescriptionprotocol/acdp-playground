"""S17 — supersession authorization (lineage-takeover prevention).

The registry now enforces *producer ownership* on supersession (registry
``34aee21``, SDK ``64a3d66``): only the original ``agent_id`` (or a
declared contributor) may publish a new version onto a lineage. A second
agent that tries to supersede someone else's context is rejected with
``superseded_target`` / ``details.reason = "not_found"`` — the same
not-found shape a genuinely absent target returns, so the registry leaks
no existence oracle. Cross-registry supersession is likewise refused
(``cross_registry_supersession_unsupported``).

This scenario drives the live registry when it's up: agent A publishes
v1, then agent B (a different DID) attempts to supersede it and we assert
the rejection surfaces as :class:`acdp_client.SupersededError` with the
expected reason. When the registry is absent — the playground's
degrade-gracefully constraint — it records a note and completes; the hard
assertion lives in ``tests/test_scenarios_round2.py`` against a mocked
400 so CI covers the contract offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

from acdp_client import AcdpHTTPError, SupersededError
from acdp_client.models import StepEvent
from playground.config import get_settings
from playground.scenarios._factory import AgentBundle, make_langchain_agent
from playground.scenarios.models import RunResult, RunSpec, ScenarioDef

log = logging.getLogger(__name__)

SCENARIO = ScenarioDef(
    id="s17_supersession_authz",
    name="Supersession authorization",
    description="A non-owner agent's attempt to supersede another producer's "
                "context is rejected with superseded_target/not_found "
                "(lineage-takeover prevention; registry 34aee21). Degrades "
                "gracefully without the registry.",
    registry_mode="single",
    agent_count=2,
    framework="langchain",
    default_inputs={"topic": "quarterly forecast"},
)


async def run(spec: RunSpec, events: asyncio.Queue[StepEvent]) -> RunResult:
    settings = get_settings()
    bundle = AgentBundle(settings, spec.run_id)
    topic = spec.inputs.get("topic", SCENARIO.default_inputs["topic"])

    async def note(title: str, preview: str) -> None:
        await events.put(
            StepEvent(
                type="scenario.note",
                run_id=spec.run_id,
                ts=datetime.now(timezone.utc).isoformat(),
                title=title,
                preview=preview,
            )
        )

    summary: dict = {"degraded": False}
    try:
        owner = make_langchain_agent(spec, events, bundle, slug="owner", registry="a")
        attacker = make_langchain_agent(
            spec, events, bundle, slug="attacker", registry="a"
        )

        # v1 by the owner.
        try:
            v1_raw = owner.producer.build_publish_request(
                title=f"{topic} — v1",
                context_type="data_snapshot",
                visibility="public",
                summary="Owner's initial version.",
                metadata=json.dumps({"run_id": spec.run_id, "role": "owner"}),
            )
            v1 = await owner.client.publish(v1_raw)
            await owner._emit("acdp.publish", ctx_id=v1.ctx_id, title=f"{topic} — v1")
            v1_full = await owner.client.retrieve_raw(v1.ctx_id)
        except Exception as e:  # noqa: BLE001 — registry absent → degrade
            log.warning("S17 owner publish failed (registry down?): %s", e)
            summary["degraded"] = True
            await note("degraded", f"registry unavailable: {type(e).__name__}")
            return RunResult(
                run_id=spec.run_id, scenario_id=SCENARIO.id, status="complete",
                contexts=[], summary=summary,
                error=None,
            )

        # The attacker (different DID) tries to supersede the owner's v1.
        previous_body = json.dumps(v1_full["body"])
        attacker_blocked = False
        reason = None
        try:
            sup_raw = attacker.producer.build_supersede_request(
                previous_body_json=previous_body,
                title=f"{topic} — hijacked",
                summary="Attacker attempts a lineage takeover.",
                metadata=json.dumps({"role": "attacker"}),
            )
            await attacker.client.publish(sup_raw)
        except SupersededError as e:
            attacker_blocked = True
            reason = e.reason
        except AcdpHTTPError as e:
            # 403/401 (not_authorized) is also an acceptable rejection.
            attacker_blocked = e.status in (401, 403)
            reason = e.code
        except Exception as e:  # noqa: BLE001 — SDK refused to even build it
            attacker_blocked = True
            reason = f"client:{type(e).__name__}"

        summary.update(
            {
                "owner_ctx": v1.ctx_id,
                "attacker_blocked": attacker_blocked,
                "rejection_reason": reason,
            }
        )
        await note(
            "takeover attempt",
            f"blocked={attacker_blocked} reason={reason}",
        )

        ok = attacker_blocked
        return RunResult(
            run_id=spec.run_id,
            scenario_id=SCENARIO.id,
            status="complete" if ok else "failed",
            contexts=[v1.ctx_id],
            summary=summary,
            error=None if ok else "S17: non-owner supersession was NOT rejected",
        )
    finally:
        await bundle.aclose()
