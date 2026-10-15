"""Record each /ask as a Langfuse trace, with every LLM call as a nested generation.

Optional: off without keys. Each trace is named ``ask`` and carries the user
and session ids, so the Langfuse Users and Sessions views work.

Langfuse v3 is OpenTelemetry based. It gets its own, non-global
``TracerProvider`` so our HTTP and DB spans are not shipped to Langfuse and its
spans do not land in Jaeger. The generation reuses our trace id, so a trace
found in Jaeger is the same id in Langfuse.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import structlog
from opentelemetry.sdk.trace import TracerProvider

from app.core.config import Settings
from app.core.context import get_request_id, get_session_id, get_user_id
from app.core.telemetry import current_trace_id
from app.llm.types import ChatMessage, LLMResult, ResolvedModel

logger = structlog.get_logger(__name__)

# The open `ask` span of the current request; LLM generations nest under it.
_root: ContextVar[Any | None] = ContextVar("langfuse_root", default=None)


class RequestTrace:
    """Handle the route uses to finish the request's Langfuse trace."""

    def __init__(self, span: Any | None) -> None:
        self._span = span

    def finish(self, *, output: Any, tags: list[str], error: str | None = None) -> None:
        if self._span is None:
            return
        try:
            self._span.update(
                output=output,
                level="ERROR" if error else None,
                status_message=error,
            )
            self._span.update_trace(output=output, tags=tags)
        except Exception:
            logger.debug("langfuse finish failed", exc_info=True)


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
                base_url=settings.langfuse_host,
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

    @contextmanager
    def request(self, *, name: str, question: str) -> Iterator[RequestTrace]:
        """Open the request's root span (named after the route). Never raises."""
        span: Any | None = None
        try:
            trace_id = current_trace_id()
            span = self._client.start_span(
                trace_context={"trace_id": trace_id} if trace_id else None,
                name=name,
                input={"question": question},
                metadata={"request_id": get_request_id()},
            )
            span.update_trace(
                name=name,
                user_id=get_user_id(),
                session_id=get_session_id(),
                input={"question": question},
                metadata={"request_id": get_request_id()},
            )
        except Exception:
            logger.debug("langfuse request start failed", exc_info=True)
            span = None
        token = _root.set(span)
        try:
            yield RequestTrace(span)
        finally:
            _root.reset(token)
            if span is not None:
                try:
                    span.end()
                except Exception:
                    logger.debug("langfuse request end failed", exc_info=True)

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
            parent = _root.get()
            if parent is not None:
                start = parent.start_generation
                where: dict[str, Any] = {}
            else:
                trace_id = current_trace_id()
                start = self._client.start_generation
                where = {"trace_context": {"trace_id": trace_id} if trace_id else None}
            generation = start(
                **where,
                name=f"llm.call {model.id} #{attempt}",
                model=model.spec.provider_model,
                input=[{"role": m.role, "content": m.content} for m in messages],
                metadata={
                    "model_id": model.id,
                    "provider": model.spec.provider.value,
                    "tier": model.spec.tier.value,
                    "attempt": attempt,
                    "request_id": get_request_id(),
                    "session_id": get_session_id(),
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
