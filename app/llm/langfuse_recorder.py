"""Record each LLM call as a Langfuse generation (optional, off without keys).

Langfuse v3 is OpenTelemetry based. It gets its own, non-global
``TracerProvider`` so our HTTP and DB spans are not shipped to Langfuse and its
spans do not land in Jaeger. The generation reuses our trace id, so a trace
found in Jaeger is the same id in Langfuse.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog
from opentelemetry.sdk.trace import TracerProvider

from app.core.config import Settings
from app.core.context import get_request_id, get_user_id
from app.core.telemetry import current_trace_id
from app.llm.types import ChatMessage, LLMResult, ResolvedModel

logger = structlog.get_logger(__name__)


class LangfuseRecorder:
    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> LangfuseRecorder | None:
        if not settings.langfuse_enabled:
            return None
        try:
            from langfuse import Langfuse

            client = Langfuse(
                public_key=settings.langfuse_public_key,
                secret_key=settings.langfuse_secret_key.get_secret_value(),
                host=settings.langfuse_host,
                environment=settings.app_env,
                release=settings.service_version,
                tracer_provider=TracerProvider(),
            )
        except Exception:
            logger.warning(
                "langfuse init failed; LLM generations will not be recorded", exc_info=True
            )
            return None
        logger.info("langfuse enabled", host=settings.langfuse_host)
        return cls(client)

    def record(
        self,
        *,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        attempt: int,
        result: LLMResult | None,
        error: Exception | None,
    ) -> None:
        """Never raises: losing an LLM trace must not fail the request."""
        try:
            trace_id = current_trace_id()
            generation = self._client.start_generation(
                trace_context={"trace_id": trace_id} if trace_id else None,
                name="llm.call",
                model=model.spec.provider_model,
                input=[{"role": m.role, "content": m.content} for m in messages],
                metadata={
                    "model_id": model.id,
                    "provider": model.spec.provider.value,
                    "tier": model.spec.tier.value,
                    "attempt": attempt,
                    "request_id": get_request_id(),
                    "user_id": get_user_id(),
                },
            )
            if result is not None:
                generation.update(
                    output=result.text,
                    usage_details={"input": result.input_tokens, "output": result.output_tokens},
                )
            else:
                generation.update(level="ERROR", status_message=str(error))
            generation.end()
        except Exception:
            logger.debug("langfuse record failed", exc_info=True)

    def shutdown(self) -> None:
        try:
            self._client.flush()
            self._client.shutdown()
        except Exception:
            logger.warning("langfuse shutdown failed", exc_info=True)
