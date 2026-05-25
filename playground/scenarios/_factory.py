"""Shared helpers used by scenario implementations.

Centralizes the boilerplate of: deterministic identity from
``run_id+slug``, picking the right :class:`AcdpClient` per registry,
optional bearer-token auth, and building a per-run authority → client
map for cross-registry resolution.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from acdp import AcdpProducer
from acdp_client import AcdpClient, TokenManager
from acdp_client.models import StepEvent

from playground.agents import BasePlaygroundAgent, LangChainAgent
from playground.config import Settings, get_settings
from playground.scenarios.models import RunSpec

Registry = Literal["a", "b"]


def did_for(authority: str, slug: str) -> str:
    return f"did:web:{authority}:agents:{slug}"


def key_id_for(authority: str, slug: str) -> str:
    return f"{did_for(authority, slug)}#key-1"


def producer_for(spec: RunSpec, slug: str, authority: str) -> AcdpProducer:
    seed = spec.agent_seed(slug)
    return AcdpProducer.from_seed(seed, did_for(authority, slug), key_id_for(authority, slug))


class AgentBundle:
    """Per-run cache of AcdpClient instances + authority map.

    Each ``(registry, producer-or-anonymous)`` pair gets its own
    :class:`AcdpClient`; cross-registry retrieval is wired up via
    :meth:`authority_map`. Tokens are minted lazily via the shared
    :class:`TokenManager`.
    """

    def __init__(self, settings: Settings, run_id: str):
        self._settings = settings
        self._run_id = run_id
        # Key: (registry, producer_did_or_None) — anonymous clients
        # cache under None.
        self._clients: dict[tuple[Registry, str | None], AcdpClient] = {}
        self._token_manager: TokenManager | None = None

    def _registry_url(self, registry: Registry) -> str:
        return (
            self._settings.registry_a_url if registry == "a"
            else self._settings.registry_b_url
        )

    def _ensure_token_manager(self) -> TokenManager:
        if self._token_manager is None:
            self._token_manager = TokenManager()
        return self._token_manager

    def client(
        self,
        registry: Registry,
        *,
        producer: AcdpProducer | None = None,
    ) -> AcdpClient:
        """Return an :class:`AcdpClient` for the registry, authenticated
        as ``producer`` when supplied.
        """
        key: tuple[Registry, str | None] = (
            registry,
            producer.agent_did if producer else None,
        )
        if key not in self._clients:
            kwargs: dict = {"run_id": self._run_id}
            if producer is not None:
                kwargs["producer"] = producer
                kwargs["token_manager"] = self._ensure_token_manager()
            self._clients[key] = AcdpClient(self._registry_url(registry), **kwargs)
        return self._clients[key]

    def anonymous_client(self, registry: Registry) -> AcdpClient:
        """Anonymous client for the registry — for outsider-perspective
        tests that should be denied access to restricted contexts.
        """
        return self.client(registry)

    def authority_map(self, *, producer: AcdpProducer | None = None) -> dict[str, AcdpClient]:
        """Map authority → client for cross-registry resolution.

        When ``producer`` is supplied, both authority clients are
        authenticated as that producer; otherwise anonymous clients
        are used.
        """
        return {
            self._settings.registry_a_authority: self.client("a", producer=producer),
            self._settings.registry_b_authority: self.client("b", producer=producer),
        }

    async def aclose(self) -> None:
        await asyncio.gather(*(c.aclose() for c in self._clients.values()))
        if self._token_manager is not None:
            await self._token_manager.aclose()


def make_langchain_agent(
    spec: RunSpec,
    events: asyncio.Queue[StepEvent],
    bundle: AgentBundle,
    *,
    slug: str,
    registry: Registry = "a",
    authority: str | None = None,
    authenticated: bool = False,
) -> LangChainAgent:
    """Build a LangChain-backed agent.

    Set ``authenticated=True`` to attach a :class:`TokenManager` —
    required for publishing/retrieving restricted-visibility contexts.
    """
    settings = get_settings()
    auth = authority or (
        settings.registry_a_authority if registry == "a" else settings.registry_b_authority
    )
    producer = producer_for(spec, slug, auth)
    client = bundle.client(registry, producer=producer if authenticated else None)
    return LangChainAgent(
        producer,
        client,
        events,
        spec.run_id,
        authority_map=bundle.authority_map(
            producer=producer if authenticated else None
        ),
        slug=slug,
    )


__all__ = [
    "AgentBundle",
    "did_for",
    "key_id_for",
    "make_langchain_agent",
    "producer_for",
]
