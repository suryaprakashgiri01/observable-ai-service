"""The provider protocol and the HTTP error mapping every adapter shares."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import httpx

from app.llm.types import (
    ChatMessage,
    LLMResult,
    PermanentLLMError,
    ResolvedModel,
    TransientLLMError,
)


class LLMProvider(Protocol):
    async def complete(
        self,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> LLMResult: ...


async def post_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    """POST and classify failures, so retry policy never needs to know a vendor."""
    try:
        response = await client.post(url, headers=headers, json=payload, timeout=timeout_seconds)
    except httpx.TimeoutException as exc:
        raise TransientLLMError(f"timed out after {timeout_seconds}s", reason="timeout") from exc
    except httpx.TransportError as exc:
        raise TransientLLMError(f"transport error: {type(exc).__name__}", reason="network") from exc

    status = response.status_code
    if status == 429:
        raise TransientLLMError("rate limited", reason="rate_limited")
    if status >= 500:
        raise TransientLLMError(f"provider returned {status}", reason="server_error")
    if status in (401, 403):
        raise PermanentLLMError(f"provider rejected credentials ({status})", reason="auth")
    if status >= 400:
        # The body explains the 400 (unknown model, bad param); keep it short.
        raise PermanentLLMError(
            f"provider returned {status}: {response.text[:300]}", reason="bad_request"
        )
    try:
        body: dict[str, Any] = response.json()
    except ValueError as exc:
        raise PermanentLLMError("provider returned non-JSON", reason="invalid_response") from exc
    return body


def require_key(model: ResolvedModel) -> str:
    key = model.api_key.get_secret_value() if model.api_key else ""
    if not key:
        raise PermanentLLMError(f"no API key configured for {model.id}", reason="auth")
    return key
