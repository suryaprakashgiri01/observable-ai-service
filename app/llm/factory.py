"""Build the provider map once per process; all providers share one HTTP client."""

from __future__ import annotations

import httpx

from app.core.config import Settings
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.base import LLMProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.mock import MockProvider
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.types import Provider


def build_http_client(settings: Settings) -> httpx.AsyncClient:
    # Per-call timeouts come from the orchestrator; this is the connection-level guard.
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.llm_timeout_seconds, connect=5.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )


def build_providers(client: httpx.AsyncClient, settings: Settings) -> dict[Provider, LLMProvider]:
    return {
        Provider.OPENAI: OpenAICompatibleProvider(
            client,
            default_base_url=settings.openai_base_url,
            token_field="max_completion_tokens",
            json_mode=True,
        ),
        Provider.BYOM: OpenAICompatibleProvider(client, key_required=False),
        Provider.ANTHROPIC: AnthropicProvider(client, default_base_url=settings.anthropic_base_url),
        Provider.GOOGLE: GeminiProvider(client, default_base_url=settings.gemini_base_url),
        Provider.MOCK: MockProvider(),
    }
