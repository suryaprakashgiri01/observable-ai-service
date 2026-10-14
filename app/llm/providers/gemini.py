"""Google Gemini ``generateContent`` over raw HTTP."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.llm.providers.base import post_json, require_key
from app.llm.types import ChatMessage, LLMResult, PermanentLLMError, ResolvedModel

# 2.5 models think, and thinking tokens count against maxOutputTokens.
_MIN_MAX_TOKENS = 2048
_BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION"})


class GeminiProvider:
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
            "contents": [
                {
                    "role": "model" if m.role == "assistant" else "user",
                    "parts": [{"text": m.content}],
                }
                for m in messages
                if m.role != "system"
            ],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max(max_tokens, _MIN_MAX_TOKENS),
                "responseMimeType": "application/json",
            },
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        body = await post_json(
            self._client,
            f"{base_url}/models/{model.spec.provider_model}:generateContent",
            headers={"x-goog-api-key": require_key(model), "Content-Type": "application/json"},
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        candidates = body.get("candidates") or []
        if not candidates:
            raise PermanentLLMError("response had no candidates", reason="invalid_response")
        candidate = candidates[0]
        if candidate.get("finishReason") in _BLOCKED_FINISH_REASONS:
            raise PermanentLLMError("model declined the request", reason="refusal")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        usage = body.get("usageMetadata") or {}
        return LLMResult(
            text=text,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
        )
