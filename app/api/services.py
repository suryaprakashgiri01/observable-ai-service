"""Dependencies that build request-scoped services from app state."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import AppSettings, Session
from app.llm.orchestrator import LLMOrchestrator
from app.llm.registry import ModelRegistry


def _registry(session: Session, settings: AppSettings) -> ModelRegistry:
    return ModelRegistry(session, settings)


def _orchestrator(request: Request) -> LLMOrchestrator:
    orchestrator: LLMOrchestrator = request.app.state.orchestrator
    return orchestrator


Registry = Annotated[ModelRegistry, Depends(_registry)]
Orchestrator = Annotated[LLMOrchestrator, Depends(_orchestrator)]
