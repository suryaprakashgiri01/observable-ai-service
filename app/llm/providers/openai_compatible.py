"""OpenAI Chat Completions, and any endpoint that speaks the same protocol.

One adapter serves both OpenAI itself and bring-your-own models (Ollama, vLLM,
OpenRouter, LM Studio...). They differ only in auth, the token-limit field name,
and whether JSON mode is understood.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.llm.providers.base import post_json, require_key
from app.llm.types import ChatMessage, LLMResult, PermanentLLMError, ResolvedModel


class OpenAICompatibleProvider:
    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        default_base_url: str | None = None,
        key_required: bool = True,
        # OpenAI renamed max_tokens; most compatible servers only know the old name.
        token_field: str = "max_tokens",
        json_mode: bool = False,
    ) -> None:
        self._client = client
        self._default_base_url = default_base_url
        self._key_required = key_required
        self._token_field = token_field
        self._json_mode = json_mode

    async def complete(
        self,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        *,
        max_tokens: int,
        temperature: float,
        timeout_seconds: float,
    ) -> LLMResult:
        base_url = (model.base_url or self._default_base_url or "").rstrip("/")
        if not base_url:
            raise PermanentLLMError(f"no base URL for {model.id}", reason="bad_request")

        headers = {"Content-Type": "application/json"}
        if self._key_required:
            headers["Authorization"] = f"Bearer {require_key(model)}"
        elif model.api_key and model.api_key.get_secret_value():
            headers["Authorization"] = f"Bearer {model.api_key.get_secret_value()}"

        payload: dict[str, Any] = {
            "model": model.spec.provider_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            self._token_field: max_tokens,
        }
        if self._json_mode:
            payload["response_format"] = {"type": "json_object"}

        body = await post_json(
            self._client,
            f"{base_url}/chat/completions",
            headers=headers,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        try:
            text = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise PermanentLLMError("unexpected response shape", reason="invalid_response") from exc
        usage = body.get("usage") or {}
        return LLMResult(
            text=text,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
        )
