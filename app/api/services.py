"""Dependencies that build request-scoped services from app state."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from app.api.deps import AppSettings, Session
from app.integrations.events import EventPublisher
from app.llm.langfuse_recorder import LangfuseRecorder
from app.llm.orchestrator import LLMOrchestrator
from app.llm.registry import ModelRegistry
from app.services.jobs import JobRunner


def _registry(session: Session, settings: AppSettings) -> ModelRegistry:
    return ModelRegistry(session, settings)


def _orchestrator(request: Request) -> LLMOrchestrator:
    orchestrator: LLMOrchestrator = request.app.state.orchestrator
    return orchestrator


def _langfuse(request: Request) -> LangfuseRecorder | None:
    recorder: LangfuseRecorder | None = getattr(request.app.state, "langfuse", None)
    return recorder


def _events(request: Request) -> EventPublisher:
    events: EventPublisher = request.app.state.events
    return events


def _jobs(request: Request) -> JobRunner:
    jobs: JobRunner = request.app.state.jobs
    return jobs


Registry = Annotated[ModelRegistry, Depends(_registry)]
Orchestrator = Annotated[LLMOrchestrator, Depends(_orchestrator)]
Langfuse = Annotated[LangfuseRecorder | None, Depends(_langfuse)]
Events = Annotated[EventPublisher, Depends(_events)]
Jobs = Annotated[JobRunner, Depends(_jobs)]
