"""Unit tests for the in-process SSE event bus (playground.events)."""

from __future__ import annotations

from acdp_client.models import StepEvent
from playground.events import create_queue, drop_queue, get_queue, publish


def _step(run_id: str) -> StepEvent:
    return StepEvent(type="scenario.note", run_id=run_id, ts="2026-06-10T00:00:00Z")


def test_create_queue_is_idempotent():
    run_id = "evt-run-create"
    try:
        q1 = create_queue(run_id)
        q2 = create_queue(run_id)
        assert q1 is q2  # same run_id returns the same queue, not a fresh one
        assert get_queue(run_id) is q1
    finally:
        drop_queue(run_id)


def test_get_queue_unknown_returns_none():
    assert get_queue("never-created") is None


def test_drop_queue_is_safe_when_absent():
    drop_queue("never-created")  # no KeyError


async def test_publish_delivers_to_matching_queue():
    run_id = "evt-run-deliver"
    queue = create_queue(run_id)
    try:
        await publish(_step(run_id))
        got = queue.get_nowait()
        assert got.run_id == run_id
        assert got.type == "scenario.note"
    finally:
        drop_queue(run_id)


async def test_publish_to_unknown_run_is_dropped_silently():
    # No queue for this run_id → publish must not raise.
    await publish(_step("no-such-run"))


async def test_dropped_queue_no_longer_receives():
    run_id = "evt-run-dropped"
    queue = create_queue(run_id)
    drop_queue(run_id)
    await publish(_step(run_id))  # bus has forgotten the run
    assert queue.empty()
