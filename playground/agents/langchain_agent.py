"""LangChain-backed agent.

Defaults to OpenAI per the playground's settings; can run against
Anthropic by setting ``LLM_PROVIDER=anthropic``. Falls through to the
deterministic mock when ``LLM_PROVIDER=mock``.
"""

from __future__ import annotations

from typing import Any

from playground.agents.base import BasePlaygroundAgent, build_llm
from playground.config import get_settings


class LangChainAgent(BasePlaygroundAgent):
    framework = "langchain"

    def __init__(self, *args: Any, llm: Any = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        if llm is None:
            s = get_settings()
            api_key = s.openai_api_key if s.llm_provider == "openai" else s.anthropic_api_key
            llm = build_llm(s.llm_provider, s.llm_model, api_key=api_key)
        self.llm = llm

    async def call_llm(self, prompt: str) -> str:
        resp = await self.llm.ainvoke(prompt)
        return resp.content if hasattr(resp, "content") else str(resp)
