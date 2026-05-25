"""Settings via pydantic-settings (.env file aware)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Registries
    registry_a_url: str = "http://localhost:8100"
    registry_b_url: str = "http://localhost:8200"
    registry_a_authority: str = "registry-a.playground.local"
    registry_b_authority: str = "registry-b.playground.local"

    # LLM provider
    llm_provider: Literal["openai", "anthropic", "mock"] = "openai"
    llm_model: str = "gpt-4o-mini"
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Optional control plane
    control_plane_url: str = ""
    control_plane_hmac_secret: str = ""

    # Webhook signing — must match the value the registries are launched with
    webhook_secret: str = "playground-dev-secret"

    # Logging
    log_format: Literal["pretty", "json"] = "pretty"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── derived ──────────────────────────────────────────────────────────

    def registry_url_for(self, authority: str) -> str | None:
        if authority == self.registry_a_authority:
            return self.registry_a_url
        if authority == self.registry_b_authority:
            return self.registry_b_url
        return None

    def authority_url_map(self) -> dict[str, str]:
        return {
            self.registry_a_authority: self.registry_a_url,
            self.registry_b_authority: self.registry_b_url,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
