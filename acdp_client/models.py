"""Pydantic aliases for the ACDP wire types the registry returns.

These mirror the JSON shapes documented by acdp-registry-rs. Fields
are deliberately permissive (extra = "allow") because the protocol
ships forward-compatible additions.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class Signature(_Open):
    algorithm: str
    key_id: str
    value: str


class Body(_Open):
    """Full ACDP context body (signed payload)."""

    ctx_id: str
    lineage_id: str
    origin_registry: str
    created_at: datetime
    content_hash: str
    signature: Signature
    version: int
    agent_id: str
    title: str
    type: str
    visibility: str
    derived_from: list[str] = Field(default_factory=list)
    data_refs: list[Any] = Field(default_factory=list)
    contributors: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    audience: list[str] | None = None
    description: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    domain: str | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] | None = None
    schema_uri: str | None = None


class RegistryState(_Open):
    status: str


class FullContext(_Open):
    body: Body
    registry_state: RegistryState
    registry_receipt: dict[str, Any] | None = None


class PublishResponse(_Open):
    ctx_id: str
    lineage_id: str
    version: int
    created_at: datetime
    status: str


class SearchHit(_Open):
    ctx_id: str
    title: str | None = None
    agent_id: str | None = None
    context_type: str | None = None
    visibility: str | None = None
    lineage_id: str | None = None
    summary: str | None = None


class SearchResponse(_Open):
    # The registry returns this list under the key `matches`; expose
    # it under both names so callers can use whichever reads better.
    matches: list[SearchHit] = Field(default_factory=list)
    total_estimate: int | None = None
    next_cursor: str | None = None

    @property
    def results(self) -> list[SearchHit]:
        return self.matches


# Cursor error codes per RFC-ACDP-0005 / RFC-ACDP-0007 §4.
CURSOR_ERROR_CODES = frozenset({"invalid_cursor", "cursor_expired"})


class CursorError(RuntimeError):
    """A pagination cursor was rejected by the registry.

    Carries the wire error ``code`` (``invalid_cursor`` or
    ``cursor_expired``) so callers can decide whether to restart
    pagination from the beginning (expired) or treat the cursor as a
    bug (invalid).
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# ── Webhook + SSE event types ────────────────────────────────────────────


WebhookType = Literal[
    "context_published",
    "context_retrieved",
    "search_executed",
]


class WebhookEvent(_Open):
    """Event shape posted by acdp-registry-rs to subscribers.

    The registry wraps each event in a small envelope (``event_id`` +
    ``schema_version``) and carries the tenant out-of-band in the
    ``X-Tenant-Id`` header (never in the signed body). ``event_id`` is
    retry-stable so downstream consumers can de-duplicate redeliveries.
    ``tenant_id`` is populated by the receiver from the header, not the
    wire body.
    """

    type: WebhookType
    # `context_published` always carries an agent; `context_retrieved` and
    # `search_executed` may be agent-less (CP REG fix 4345daf), so this is
    # optional rather than required.
    agent_id: str | None = None
    registry_authority: str | None = None
    run_id: str | None = None
    ctx_id: str | None = None
    lineage_id: str | None = None
    context_type: str | None = None
    visibility: str | None = None
    version: int | None = None
    created_at: datetime | None = None
    derived_from: list[str] = Field(default_factory=list)
    # Envelope / routing metadata.
    event_id: str | None = None
    schema_version: str | None = None
    tenant_id: str | None = None


StepEventType = Literal[
    "agent.started",
    "llm.thinking",
    "acdp.publish",
    "acdp.retrieve",
    "acdp.search",
    "acdp.verify",
    "auth.token",
    "auth.revoke",
    "policy.check",
    "scenario.note",
    "run.started",
    "run.complete",
    "run.error",
    "webhook.received",
]


class StepEvent(_Open):
    """Event broadcast over SSE for one run."""

    type: StepEventType
    run_id: str
    ts: str
    agent_id: str | None = None
    ctx_id: str | None = None
    title: str | None = None
    derived_from: list[str] = Field(default_factory=list)
    preview: str | None = None
    contexts_produced: int | None = None
    lineage_graph: dict[str, Any] | None = None
    error: str | None = None
    scenario_id: str | None = None
    framework: str | None = None
    registry_authority: str | None = None
    tenant_id: str | None = None
    event_id: str | None = None

    @classmethod
    def from_webhook(cls, run_id: str, ts: str, event: WebhookEvent) -> "StepEvent":
        kind = {
            "context_published": "acdp.publish",
            "context_retrieved": "acdp.retrieve",
            "search_executed": "acdp.search",
        }[event.type]
        return cls(
            type=kind,
            run_id=run_id,
            ts=ts,
            agent_id=event.agent_id,
            ctx_id=event.ctx_id,
            derived_from=event.derived_from,
            registry_authority=event.registry_authority,
            tenant_id=event.tenant_id,
            event_id=event.event_id,
        )
