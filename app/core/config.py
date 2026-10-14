"""Service settings, read once from the environment (and `.env` locally)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "observable-ai-service"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- service ---------------------------------------------------------
    app_env: Literal["local", "test", "dev", "prod"] = "local"
    service_version: str = "0.1.0"
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    log_level: str = "INFO"
    log_format: Literal["json", "console", "auto"] = "auto"
    # Honour X-Debug-Fault. A demo/chaos switch; never on in prod.
    enable_fault_injection: bool = False
    # How long an injected `llm_timeout` stalls before failing, per attempt.
    fault_injection_delay_seconds: float = Field(default=1.0, ge=0)

    # --- database ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/ai_service"
    db_echo: bool = False
    # Fernet key for bring-your-own-model API keys. Empty disables storing them.
    app_encryption_key: SecretStr = SecretStr("")

    # --- LLM ---------------------------------------------------------------
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    llm_default_model_id: str = "gpt-4.1-mini"
    llm_fallback_model_id: str = "mock-echo"
    llm_timeout_seconds: float = Field(default=20.0, gt=0)
    llm_max_attempts: int = Field(default=3, ge=1, le=6)
    llm_backoff_base_seconds: float = Field(default=0.5, ge=0)
    llm_max_tokens: int = Field(default=512, ge=16)
    llm_temperature: float = Field(default=0.2, ge=0, le=2)
    max_custom_models_per_user: int = 10
    # Upsert the built-in catalog on startup, so a fresh database is usable at once.
    auto_sync_catalog: bool = True

    # --- tracing -----------------------------------------------------------
    otel_enabled: bool = True
    otel_service_name: str = SERVICE_NAME
    # Base endpoint; the exporter appends /v1/traces. Empty = spans stay in-process.
    otel_exporter_otlp_endpoint: str = ""
    otel_excluded_urls: str = "health,metrics"

    # --- Langfuse ----------------------------------------------------------
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = SecretStr("")
    langfuse_host: str = "https://cloud.langfuse.com"

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_public_key and self.langfuse_secret_key.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
