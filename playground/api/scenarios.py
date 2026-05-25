"""GET /scenarios — catalog of runnable scenarios."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from playground.scenarios import get_scenario, list_scenarios

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


def _serialize(s) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "registry_mode": s.registry_mode,
        "agent_count": s.agent_count,
        "framework": s.framework,
        "default_inputs": s.default_inputs,
    }


@router.get("")
async def list_all() -> dict:
    return {"scenarios": [_serialize(s) for s in list_scenarios()]}


@router.get("/{scenario_id}")
async def get_one(scenario_id: str) -> dict:
    s = get_scenario(scenario_id)
    if s is None:
        raise HTTPException(404, f"unknown scenario: {scenario_id}")
    return _serialize(s)
