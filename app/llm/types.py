"""Provider-neutral types shared by the catalog, providers and orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from pydantic import SecretStr


class Provider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    BYOM = "byom"
    MOCK = "mock"


class Tier(StrEnum):
    ECONOMY = "economy"
    STANDARD = "standard"
    PREMIUM = "premium"


Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A model as the caller sees it: our id, mapped to the provider's own name."""

    id: str
    name: str
    provider: Provider
    tier: Tier
    provider_model: str


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    """A model ready to call: the spec plus where and how to reach it."""

    spec: ModelSpec
    base_url: str | None = None
    api_key: SecretStr | None = field(default=None, repr=False)

    @property
    def id(self) -> str:
        return self.spec.id


@dataclass(frozen=True, slots=True)
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class LLMError(Exception):
    """Base for provider failures. ``reason`` is a bounded metric label."""

    reason: str = "error"

    def __init__(self, message: str, *, reason: str | None = None) -> None:
        super().__init__(message)
        if reason is not None:
            self.reason = reason


class TransientLLMError(LLMError):
    """Worth retrying: timeout, 429, 5xx, connection reset."""

    reason = "transient"


class PermanentLLMError(LLMError):
    """Retrying cannot help: bad key, bad request, unparseable response."""

    reason = "permanent"
