"""Run a completion across a fallback chain, retrying transient failures.

Chain: the selected model, then the fallback model, then ``mock-echo``. Each
model gets up to ``LLM_MAX_ATTEMPTS`` tries on transient errors (timeout, 429,
5xx) with jittered exponential backoff; a permanent error (bad key, bad request,
refusal) moves straight to the next model. Every attempt is its own
``llm.call`` span, so a slow request reads in Jaeger as "three 20 s timeouts,
then a fallback" rather than one opaque 60 s bar.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import structlog
from opentelemetry import trace
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import Settings
from app.core.metrics import LLM_FALLBACKS, LLM_LATENCY, LLM_REQUESTS, LLM_RETRIES, LLM_TOKENS
from app.core.telemetry import tracer
from app.exceptions import AppError, ErrorCode
from app.llm.catalog import CATALOG, MOCK_MODEL_ID
from app.llm.langfuse_recorder import LangfuseRecorder
from app.llm.providers.base import LLMProvider
from app.llm.registry import Selection
from app.llm.types import (
    ChatMessage,
    LLMError,
    LLMResult,
    PermanentLLMError,
    Provider,
    ResolvedModel,
    TransientLLMError,
)

logger = structlog.get_logger(__name__)

FAULT_LLM_TIMEOUT = "llm_timeout"
FAULT_LLM_ERROR = "llm_error"

_MOCK = ResolvedModel(spec=next(s for s in CATALOG if s.id == MOCK_MODEL_ID))


@dataclass(frozen=True, slots=True)
class Completion:
    result: LLMResult
    model: ResolvedModel
    attempts: int
    fallback_used: bool
    chain: list[str] = field(default_factory=list)


class LLMOrchestrator:
    def __init__(
        self,
        providers: Mapping[Provider, LLMProvider],
        settings: Settings,
        recorder: LangfuseRecorder | None = None,
    ) -> None:
        self._providers = providers
        self._settings = settings
        self._recorder = recorder

    async def complete(
        self,
        selection: Selection,
        messages: Sequence[ChatMessage],
        *,
        fault: str | None = None,
    ) -> Completion:
        chain = [selection.model]
        if selection.fallback is not None:
            chain.append(selection.fallback)
        if all(m.id != MOCK_MODEL_ID for m in chain):
            chain.append(_MOCK)

        attempts = 0
        tried: list[str] = []
        for index, model in enumerate(chain):
            tried.append(model.id)
            # Faults hit only the selected model, so the fallback path is what the demo shows.
            model_fault = fault if index == 0 else None
            try:
                result, used = await self._with_retries(model, messages, model_fault)
                attempts += used
                return Completion(
                    result=result,
                    model=model,
                    attempts=attempts,
                    fallback_used=index > 0,
                    chain=tried,
                )
            except LLMError as exc:
                attempts += exc.attempts
                if index + 1 < len(chain):
                    nxt = chain[index + 1]
                    LLM_FALLBACKS.labels(
                        from_model=model.id, to_model=nxt.id, reason=exc.reason
                    ).inc()
                    trace.get_current_span().add_event(
                        "llm.fallback",
                        {"from_model": model.id, "to_model": nxt.id, "reason": exc.reason},
                    )
                    logger.warning(
                        "llm model failed, falling back",
                        from_model=model.id,
                        to_model=nxt.id,
                        reason=exc.reason,
                        error=str(exc),
                    )
        raise AppError(ErrorCode.LLM_UNAVAILABLE, "No model in the fallback chain answered")

    async def _with_retries(
        self, model: ResolvedModel, messages: Sequence[ChatMessage], fault: str | None
    ) -> tuple[LLMResult, int]:
        def _before_sleep(state: RetryCallState) -> None:
            LLM_RETRIES.labels(model=model.id).inc()
            exc = state.outcome.exception() if state.outcome else None
            logger.warning(
                "llm call failed, retrying",
                model_id=model.id,
                attempt=state.attempt_number,
                reason=getattr(exc, "reason", "unknown"),
                sleep_seconds=round(state.next_action.sleep, 3) if state.next_action else None,
            )

        attempt = 0
        try:
            async for retry in AsyncRetrying(
                stop=stop_after_attempt(self._settings.llm_max_attempts),
                wait=wait_exponential_jitter(
                    initial=self._settings.llm_backoff_base_seconds, max=4.0
                ),
                retry=retry_if_exception_type(TransientLLMError),
                before_sleep=_before_sleep,
                reraise=True,
            ):
                with retry:
                    attempt = retry.retry_state.attempt_number
                    return await self._attempt(model, messages, attempt, fault), attempt
        except LLMError as exc:
            exc.attempts = attempt
            raise
        raise AssertionError("unreachable")  # pragma: no cover

    async def _attempt(
        self,
        model: ResolvedModel,
        messages: Sequence[ChatMessage],
        attempt: int,
        fault: str | None,
    ) -> LLMResult:
        spec = model.spec
        labels = {"provider": spec.provider.value, "model": spec.id}
        with tracer.start_as_current_span(
            "llm.call",
            kind=trace.SpanKind.CLIENT,
            attributes={
                "gen_ai.system": spec.provider.value,
                "gen_ai.request.model": spec.provider_model,
                "llm.model_id": spec.id,
                "llm.tier": spec.tier.value,
                "llm.attempt": attempt,
            },
        ) as span:
            started = time.perf_counter()
            result: LLMResult | None = None
            error: LLMError | None = None
            try:
                result = await self._call(model, messages, fault)
                return result
            except LLMError as exc:
                error = exc
                span.record_exception(exc)
                span.set_status(trace.Status(trace.StatusCode.ERROR, exc.reason))
                raise
            finally:
                elapsed = time.perf_counter() - started
                outcome = "success" if error is None else error.reason
                span.set_attribute("llm.outcome", outcome)
                LLM_LATENCY.labels(**labels).observe(elapsed)
                LLM_REQUESTS.labels(**labels, tier=spec.tier.value, outcome=outcome).inc()
                if result is not None:
                    span.set_attribute("gen_ai.usage.input_tokens", result.input_tokens)
                    span.set_attribute("gen_ai.usage.output_tokens", result.output_tokens)
                    LLM_TOKENS.labels(**labels, type="input").inc(result.input_tokens)
                    LLM_TOKENS.labels(**labels, type="output").inc(result.output_tokens)
                logger.info(
                    "llm call",
                    model_id=spec.id,
                    provider=spec.provider.value,
                    attempt=attempt,
                    outcome=outcome,
                    duration_ms=round(elapsed * 1000, 1),
                    input_tokens=result.input_tokens if result else None,
                    output_tokens=result.output_tokens if result else None,
                )
                if self._recorder is not None:
                    self._recorder.record(
                        model=model, messages=messages, attempt=attempt, result=result, error=error
                    )

    async def _call(
        self, model: ResolvedModel, messages: Sequence[ChatMessage], fault: str | None
    ) -> LLMResult:
        timeout = self._settings.llm_timeout_seconds
        if fault == FAULT_LLM_TIMEOUT:
            await asyncio.sleep(min(self._settings.fault_injection_delay_seconds, timeout))
            raise TransientLLMError(f"injected timeout after {timeout}s", reason="timeout")
        if fault == FAULT_LLM_ERROR:
            raise PermanentLLMError("injected provider error", reason="bad_request")

        provider = self._providers.get(model.spec.provider)
        if provider is None:
            raise PermanentLLMError(f"no provider for {model.spec.provider}", reason="bad_request")
        return await provider.complete(
            model,
            messages,
            max_tokens=self._settings.llm_max_tokens,
            temperature=self._settings.llm_temperature,
            timeout_seconds=timeout,
        )
