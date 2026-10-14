"""The built-in model catalog: one economy/standard/premium model per vendor.

``id`` is ours and stable; ``provider_model`` is the vendor's name and may be
re-pointed without breaking callers who pinned an id.
"""

from __future__ import annotations

from app.llm.types import ModelSpec, Provider, Tier

MOCK_MODEL_ID = "mock-echo"

CATALOG: tuple[ModelSpec, ...] = (
    # OpenAI
    ModelSpec("gpt-4.1-nano", "GPT-4.1 nano", Provider.OPENAI, Tier.ECONOMY, "gpt-4.1-nano"),
    ModelSpec("gpt-4.1-mini", "GPT-4.1 mini", Provider.OPENAI, Tier.STANDARD, "gpt-4.1-mini"),
    ModelSpec("gpt-4.1", "GPT-4.1", Provider.OPENAI, Tier.PREMIUM, "gpt-4.1"),
    # Anthropic
    ModelSpec(
        "claude-haiku-5-5", "Claude Haiku 5.5", Provider.ANTHROPIC, Tier.ECONOMY, "claude-haiku-5-5"
    ),
    ModelSpec(
        "claude-sonnet-5-5",
        "Claude Sonnet 5.5",
        Provider.ANTHROPIC,
        Tier.STANDARD,
        "claude-sonnet-5-5",
    ),
    ModelSpec(
        "claude-opus-5-5", "Claude Opus 5.5", Provider.ANTHROPIC, Tier.PREMIUM, "claude-opus-5-5"
    ),
    # Google
    ModelSpec(
        "gemini-2.5-flash-lite",
        "Gemini 2.5 Flash-Lite",
        Provider.GOOGLE,
        Tier.ECONOMY,
        "gemini-2.5-flash-lite",
    ),
    ModelSpec(
        "gemini-2.5-flash", "Gemini 2.5 Flash", Provider.GOOGLE, Tier.STANDARD, "gemini-2.5-flash"
    ),
    ModelSpec("gemini-2.5-pro", "Gemini 2.5 Pro", Provider.GOOGLE, Tier.PREMIUM, "gemini-2.5-pro"),
    # Always available: no key, no network. The last link of every fallback chain.
    ModelSpec(MOCK_MODEL_ID, "Mock (rule-based)", Provider.MOCK, Tier.ECONOMY, "mock-echo"),
)
