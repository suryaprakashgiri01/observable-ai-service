"""A per-task hook the orchestrator calls on every attempt, retry and fallback.

The orchestrator stays unaware of async jobs: the job runner sets a hook for
its own task, and synchronous requests simply have none.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

import structlog

StatusHook = Callable[[str, dict[str, Any]], Awaitable[None]]

_hook: ContextVar[StatusHook | None] = ContextVar("llm_status_hook", default=None)
logger = structlog.get_logger(__name__)


def set_status_hook(hook: StatusHook | None) -> None:
    _hook.set(hook)


async def report(status: str, **fields: Any) -> None:
    """Never raises: a failing status update must not fail the LLM call."""
    hook = _hook.get()
    if hook is None:
        return
    try:
        await hook(status, fields)
    except Exception:
        logger.warning("status hook failed", status=status, exc_info=True)
