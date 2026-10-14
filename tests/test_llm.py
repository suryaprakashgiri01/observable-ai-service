from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import AuthenticatedUser
from app.db.models import User
from app.exceptions import AppError, ErrorCode
from app.llm.catalog import CATALOG, MOCK_MODEL_ID
from app.llm.orchestrator import FAULT_LLM_ERROR, FAULT_LLM_TIMEOUT, LLMOrchestrator
from app.llm.providers.anthropic import AnthropicProvider
from app.llm.providers.gemini import GeminiProvider
from app.llm.providers.mock import MockProvider, plan_for
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.llm.registry import ModelRegistry, Selection
from app.llm.types import (
    ChatMessage,
    LLMResult,
    ModelSpec,
    PermanentLLMError,
    Provider,
    ResolvedModel,
    Tier,
    TransientLLMError,
)
from app.services.agent import parse_plan
from app.services.tools.reports import generate_report
from tests.conftest import make_settings, metric

SPEC = ModelSpec("fake-standard", "Fake", Provider.OPENAI, Tier.STANDARD, "fake-1")
FAKE = ResolvedModel(spec=SPEC, api_key=SecretStr("k"))
MOCK = ResolvedModel(spec=next(s for s in CATALOG if s.id == MOCK_MODEL_ID))
MESSAGES = [ChatMessage("system", "sys"), ChatMessage("user", "Question: hi")]


class ScriptedProvider:
    """Raises the scripted errors in order, then succeeds."""

    def __init__(self, *errors: Exception) -> None:
        self.errors = list(errors)
        self.calls = 0

    async def complete(self, model, messages, *, max_tokens, temperature, timeout_seconds):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return LLMResult(
            text='{"intent": "general", "answer": "ok"}', input_tokens=5, output_tokens=3
        )


def orchestrator(provider: ScriptedProvider) -> LLMOrchestrator:
    providers = {Provider.OPENAI: provider, Provider.MOCK: MockProvider()}
    return LLMOrchestrator(providers, make_settings())


# --- orchestrator -----------------------------------------------------------------


async def test_success_on_first_attempt(spans) -> None:
    provider = ScriptedProvider()
    done = await orchestrator(provider).complete(Selection(FAKE, "header", None), MESSAGES)
    assert done.model.id == "fake-standard" and done.attempts == 1 and not done.fallback_used
    llm_spans = [s for s in spans.get_finished_spans() if s.name == "llm.call"]
    assert len(llm_spans) == 1
    assert llm_spans[0].attributes["llm.outcome"] == "success"
    assert llm_spans[0].attributes["gen_ai.usage.input_tokens"] == 5


async def test_transient_error_is_retried_then_succeeds() -> None:
    provider = ScriptedProvider(TransientLLMError("slow", reason="timeout"))
    before = metric("llm_retries_total", model="fake-standard")
    done = await orchestrator(provider).complete(Selection(FAKE, "header", None), MESSAGES)
    assert done.model.id == "fake-standard" and done.attempts == 2
    assert metric("llm_retries_total", model="fake-standard") == before + 1


async def test_persistent_timeouts_exhaust_retries_then_fall_back(spans) -> None:
    provider = ScriptedProvider(*[TransientLLMError("t", reason="timeout") for _ in range(3)])
    labels = {"from_model": "fake-standard", "to_model": MOCK_MODEL_ID, "reason": "timeout"}
    before = metric("llm_fallbacks_total", **labels)
    done = await orchestrator(provider).complete(Selection(FAKE, "header", MOCK), MESSAGES)

    assert provider.calls == 3
    assert done.fallback_used and done.model.id == MOCK_MODEL_ID
    assert done.attempts == 4 and done.chain == ["fake-standard", MOCK_MODEL_ID]
    assert metric("llm_fallbacks_total", **labels) == before + 1
    outcomes = [
        s.attributes["llm.outcome"] for s in spans.get_finished_spans() if s.name == "llm.call"
    ]
    assert outcomes == ["timeout", "timeout", "timeout", "success"]


async def test_permanent_error_skips_retries() -> None:
    provider = ScriptedProvider(PermanentLLMError("bad key", reason="auth"))
    done = await orchestrator(provider).complete(Selection(FAKE, "header", None), MESSAGES)
    assert provider.calls == 1 and done.fallback_used and done.model.id == MOCK_MODEL_ID


async def test_mock_is_appended_as_last_resort_when_no_fallback() -> None:
    provider = ScriptedProvider(PermanentLLMError("x", reason="bad_request"))
    done = await orchestrator(provider).complete(Selection(FAKE, "body", None), MESSAGES)
    assert done.chain == ["fake-standard", MOCK_MODEL_ID]


async def test_injected_faults_hit_only_the_selected_model() -> None:
    provider = ScriptedProvider()
    orch = orchestrator(provider)
    timeout = await orch.complete(
        Selection(FAKE, "header", MOCK), MESSAGES, fault=FAULT_LLM_TIMEOUT
    )
    assert timeout.fallback_used and timeout.attempts == 4 and provider.calls == 0
    error = await orch.complete(Selection(FAKE, "header", MOCK), MESSAGES, fault=FAULT_LLM_ERROR)
    assert error.fallback_used and error.attempts == 2


# --- provider adapters (wire format and error mapping) ------------------------------


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_openai_adapter_maps_request_and_response() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content), auth=request.headers["Authorization"])
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        )

    provider = OpenAICompatibleProvider(
        _client(handler),
        default_base_url="https://x/v1",
        token_field="max_completion_tokens",
        json_mode=True,
    )
    result = await provider.complete(
        FAKE, MESSAGES, max_tokens=99, temperature=0.1, timeout_seconds=1
    )
    assert result == LLMResult("{}", 7, 2)
    assert seen["model"] == "fake-1" and seen["max_completion_tokens"] == 99
    assert seen["response_format"] == {"type": "json_object"} and seen["auth"] == "Bearer k"


@pytest.mark.parametrize(
    ("status", "error", "reason"),
    [
        (429, TransientLLMError, "rate_limited"),
        (503, TransientLLMError, "server_error"),
        (401, PermanentLLMError, "auth"),
        (400, PermanentLLMError, "bad_request"),
    ],
)
async def test_http_errors_are_classified(status, error, reason) -> None:
    provider = OpenAICompatibleProvider(
        _client(lambda r: httpx.Response(status, text="nope")), default_base_url="https://x/v1"
    )
    with pytest.raises(error) as caught:
        await provider.complete(FAKE, MESSAGES, max_tokens=1, temperature=0, timeout_seconds=1)
    assert caught.value.reason == reason


async def test_timeouts_are_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    provider = OpenAICompatibleProvider(_client(handler), default_base_url="https://x/v1")
    with pytest.raises(TransientLLMError) as caught:
        await provider.complete(FAKE, MESSAGES, max_tokens=1, temperature=0, timeout_seconds=1)
    assert caught.value.reason == "timeout"


async def test_anthropic_adapter_reads_text_blocks_and_omits_temperature() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content), version=request.headers["anthropic-version"])
        return httpx.Response(
            200,
            json={
                "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "hi"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 3, "output_tokens": 1},
            },
        )

    provider = AnthropicProvider(_client(handler), default_base_url="https://a/v1")
    result = await provider.complete(
        FAKE, MESSAGES, max_tokens=10, temperature=0.5, timeout_seconds=1
    )
    assert result.text == "hi" and result.input_tokens == 3
    assert "temperature" not in seen and seen["system"] == "sys"
    assert [m["role"] for m in seen["messages"]] == ["user"]
    assert seen["version"] == "2023-06-01"


async def test_anthropic_refusal_is_permanent() -> None:
    provider = AnthropicProvider(
        _client(lambda r: httpx.Response(200, json={"stop_reason": "refusal", "content": []})),
        default_base_url="https://a/v1",
    )
    with pytest.raises(PermanentLLMError) as caught:
        await provider.complete(FAKE, MESSAGES, max_tokens=10, temperature=0, timeout_seconds=1)
    assert caught.value.reason == "refusal"


async def test_gemini_adapter_skips_thought_parts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models/fake-1:generateContent")
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "hmm", "thought": True}, {"text": "{}"}]}}
                ],
                "usageMetadata": {"promptTokenCount": 4, "candidatesTokenCount": 1},
            },
        )

    provider = GeminiProvider(_client(handler), default_base_url="https://g/v1beta")
    result = await provider.complete(
        FAKE, MESSAGES, max_tokens=10, temperature=0, timeout_seconds=1
    )
    assert result == LLMResult("{}", 4, 1)


async def test_missing_key_is_a_permanent_auth_error() -> None:
    provider = OpenAICompatibleProvider(
        _client(lambda r: httpx.Response(200)), default_base_url="x"
    )
    keyless = ResolvedModel(spec=SPEC)
    with pytest.raises(PermanentLLMError):
        await provider.complete(keyless, MESSAGES, max_tokens=1, temperature=0, timeout_seconds=1)


# --- registry ---------------------------------------------------------------------


async def _user(session: AsyncSession, email: str = "demo@example.com") -> AuthenticatedUser:
    row = await session.scalar(select(User).where(User.email == email))
    assert row is not None
    return AuthenticatedUser(id=row.id, email=row.email, name=row.name, role=row.role)


async def test_resolution_order_header_body_user_default_global(session: AsyncSession) -> None:
    settings = make_settings(anthropic_api_key="k", llm_default_model_id="claude-sonnet-5-5")
    registry = ModelRegistry(session, settings)
    user = await _user(session)

    assert (await registry.resolve(user, header_id=None, body_id=None)).source == "global_default"
    await registry.set_default(user, "claude-haiku-5-5")
    picked = await registry.resolve(user, header_id=None, body_id=None)
    assert (picked.source, picked.model.id) == ("user_default", "claude-haiku-5-5")
    picked = await registry.resolve(user, header_id=None, body_id="claude-opus-5-5")
    assert (picked.source, picked.model.id) == ("body", "claude-opus-5-5")
    picked = await registry.resolve(user, header_id=MOCK_MODEL_ID, body_id="claude-opus-5-5")
    assert (picked.source, picked.model.id) == ("header", MOCK_MODEL_ID)


async def test_explicit_unknown_or_unavailable_model_is_rejected(session: AsyncSession) -> None:
    registry = ModelRegistry(session, make_settings())
    user = await _user(session)
    with pytest.raises(AppError) as unknown:
        await registry.resolve(user, header_id="nope", body_id=None)
    assert unknown.value.code is ErrorCode.MODEL_NOT_FOUND
    with pytest.raises(AppError) as unavailable:
        await registry.resolve(user, header_id="gpt-4.1", body_id=None)
    assert unavailable.value.code is ErrorCode.MODEL_UNAVAILABLE


async def test_unavailable_default_degrades_to_mock(session: AsyncSession) -> None:
    registry = ModelRegistry(session, make_settings(llm_default_model_id="gpt-4.1"))
    picked = await registry.resolve(await _user(session), header_id=None, body_id=None)
    assert (picked.source, picked.model.id) == ("mock", MOCK_MODEL_ID)


async def test_fallback_prefers_user_choice_and_never_equals_selected(
    session: AsyncSession,
) -> None:
    settings = make_settings(gemini_api_key="k", llm_fallback_model_id="gemini-2.5-flash-lite")
    registry = ModelRegistry(session, settings)
    user = await _user(session)
    picked = await registry.resolve(user, header_id="gemini-2.5-pro", body_id=None)
    assert picked.fallback is not None and picked.fallback.id == "gemini-2.5-flash-lite"
    await registry.set_fallback(user, MOCK_MODEL_ID)
    picked = await registry.resolve(user, header_id="gemini-2.5-pro", body_id=None)
    assert picked.fallback is not None and picked.fallback.id == MOCK_MODEL_ID
    picked = await registry.resolve(user, header_id=MOCK_MODEL_ID, body_id=None)
    assert picked.fallback is not None and picked.fallback.id == "gemini-2.5-flash-lite"


async def test_custom_models_are_private_and_key_is_encrypted(session: AsyncSession) -> None:
    registry = ModelRegistry(session, make_settings())
    owner, other = await _user(session), await _user(session, "analyst@example.com")
    await registry.register_custom(
        owner,
        model_id="my-llama",
        name="Llama",
        base_url="http://byom.test/v1",
        provider_model="llama3",
        tier=Tier.ECONOMY,
        api_key="secret-key",
    )
    resolved = await registry.require("my-llama", owner)
    assert resolved.api_key is not None and resolved.api_key.get_secret_value() == "secret-key"
    with pytest.raises(AppError):
        await registry.require("my-llama", other)
    assert all(v.spec.id != "my-llama" for v in await registry.list_for_user(other))
    from app.db.models import LLMModel

    stored = await session.get(LLMModel, "my-llama")
    assert stored is not None and stored.api_key_enc and "secret-key" not in stored.api_key_enc


async def test_list_filters_by_tier(session: AsyncSession) -> None:
    views = await ModelRegistry(session, make_settings()).list_for_user(
        await _user(session), tier=Tier.PREMIUM
    )
    assert {v.spec.provider for v in views} == {
        Provider.OPENAI,
        Provider.ANTHROPIC,
        Provider.GOOGLE,
    }


# --- agent pieces ---------------------------------------------------------------------


def test_parse_plan_tolerates_fences_and_rejects_garbage() -> None:
    plan = parse_plan('```json\n{"intent": "general", "answer": "hi"}\n```')
    assert plan is not None and plan.answer == "hi"
    assert parse_plan("no json here") is None
    assert parse_plan('{"intent": "launch_rockets"}') is None


def test_mock_plans_tickets_reports_and_general() -> None:
    ticket = plan_for("Create a critical ticket: payments are down")
    assert ticket["tool"] == "create_ticket"
    assert ticket["args"] == {"title": "payments are down", "priority": "critical"}
    report = plan_for("What was revenue in the last 30 days?")
    assert report["args"] == {"metric": "revenue", "period_days": 30}
    assert plan_for("Hello there")["intent"] == "general"


def test_report_excludes_refunds_and_clamps_period() -> None:
    report = generate_report(metric="orders", period_days=500)
    assert report.period_days == 90
    assert report.value == report.order_count
    assert sum(report.by_region.values()) == report.order_count
    assert generate_report(metric="bogus", period_days=7).metric == "revenue"


async def test_mock_provider_returns_a_valid_plan() -> None:
    result = await MockProvider().complete(
        MOCK,
        [ChatMessage("user", "Question: open a ticket: login broken")],
        max_tokens=1,
        temperature=0,
        timeout_seconds=1,
    )
    plan = parse_plan(result.text)
    assert plan is not None and plan.tool == "create_ticket"
