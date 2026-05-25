"""POST /runs, GET /runs/{id}, SSE /runs/{id}/events."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from playground.config import get_settings
from playground.control_plane import get_control_plane
from playground.events import create_queue, get_queue, drop_queue
from playground.scenarios import (
    RunRequest,
    execute,
    get_scenario,
)
from playground.scenarios.models import RunSpec
from playground.scenarios.runner import get_result

log = logging.getLogger(__name__)
router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def start_run(req: RunRequest) -> dict:
    scenario = get_scenario(req.scenario_id)
    if scenario is None:
        raise HTTPException(404, f"unknown scenario: {req.scenario_id}")

    settings = get_settings()
    run_id = str(uuid4())
    spec = RunSpec(
        run_id=run_id,
        scenario_id=req.scenario_id,
        inputs={**scenario.default_inputs, **req.inputs},
        registry_mode=req.registry_mode or scenario.registry_mode,
    )

    queue = create_queue(run_id)
    asyncio.create_task(execute(scenario, spec, queue))

    cp = get_control_plane(settings)
    asyncio.create_task(cp.notify_run_started(run_id, scenario.id, spec.inputs))

    return {
        "run_id": run_id,
        "scenario_id": scenario.id,
        "status": "running",
        "stream_url": f"/runs/{run_id}/events",
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{run_id}")
async def get_run(run_id: str) -> dict:
    result = get_result(run_id)
    in_flight = get_queue(run_id) is not None
    if result is None and not in_flight:
        raise HTTPException(404, f"unknown run: {run_id}")
    return {
        "run_id": run_id,
        "status": result.status if result else "running",
        "result": result.model_dump() if result else None,
    }


@router.get("/{run_id}/events")
async def stream_events(run_id: str, request: Request) -> StreamingResponse:
    queue = get_queue(run_id)
    if queue is None:
        # Late subscriber: the run may already be complete.
        result = get_result(run_id)
        if result is None:
            raise HTTPException(404, f"unknown run: {run_id}")

        async def replay():
            yield f"data: {result.model_dump_json()}\n\n"
            yield "event: end\ndata: complete\n\n"

        return StreamingResponse(replay(), media_type="text/event-stream")

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {event.model_dump_json()}\n\n"
                if event.type in ("run.complete", "run.error"):
                    yield "event: end\ndata: complete\n\n"
                    return
        finally:
            drop_queue(run_id)

    return StreamingResponse(generate(), media_type="text/event-stream")
