"""Anthropic Messages API over raw HTTP.

Current Claude models reject a non-default ``temperature`` and always think,
so this adapter omits sampling params, asks for ``low`` effort (this is a short
planning call), and leaves token headroom for the thinking that precedes text.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.llm.providers.base import post_json, require_key
from app.llm.types import ChatMessage, LLMResult, PermanentLLMError, ResolvedModel

ANTHROPIC_VERSION = "2023-06-01"
# Thinking tokens count against max_tokens; too small a cap truncates the answer.
_MIN_MAX_TOKENS = 4096


class AnthropicProvider:
    def __init__(self, client: httpx.AsyncClient, *, default_base_url: str) -> None:
        self._client = client
        self._default_base_url = default_base_url

    async def complete(
        self,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> LLMResult:
        base_url = (model.base_url or self._default_base_url).rstrip("/")
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        payload: dict[str, Any] = {
            "model": model.spec.provider_model,
            "max_tokens": max(max_tokens, _MIN_MAX_TOKENS),
            "messages": [
                {"role": m.role, "content": m.content} for m in messages if m.role != "system"
            ],
            "output_config": {"effort": "low"},
        }
        if system:
            payload["system"] = system

        body = await post_json(
            self._client,
            f"{base_url}/messages",
            headers={
                "x-api-key": require_key(model),
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        if body.get("stop_reason") == "refusal":
            raise PermanentLLMError("model declined the request", reason="refusal")
        # Content is a list of blocks; thinking blocks precede the text.
        text = "".join(
            block.get("text", "")
            for block in body.get("content") or []
            if block.get("type") == "text"
        )
        if not text:
            raise PermanentLLMError("response had no text block", reason="invalid_response")
        usage = body.get("usage") or {}
        return LLMResult(
            text=text,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
        )
