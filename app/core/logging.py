"""structlog configuration, bridging the stdlib so third-party loggers match.

Every line is one JSON object on stdout. ``merge_contextvars`` adds the
request_id/user_id bound by the middleware, and ``_add_trace_context`` adds the
current span's ids, so a log line can be pasted straight into Jaeger.
"""

from __future__ import annotations

import logging
import sys
from typing import Any, Literal

import structlog
from opentelemetry import trace

_EXTERNAL_LOGGERS = ("uvicorn", "uvicorn.error", "sqlalchemy.engine", "httpx", "httpcore")

# uvicorn's `color_message` carries raw ANSI escapes, which break strict JSON parsers.
_NOISY_RECORD_KEYS = ("color_message",)

# Keys whose values must never reach a log line, whatever the caller passes.
_REDACTED_KEYS = frozenset({"api_key", "x-api-key", "authorization", "password", "secret"})


def _drop_noisy_keys(
    _logger: object, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    for key in _NOISY_RECORD_KEYS:
        event_dict.pop(key, None)
    return event_dict


def _redact_secrets(
    _logger: object, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    for key in list(event_dict):
        if key.lower() in _REDACTED_KEYS:
            event_dict[key] = "[REDACTED]"
    return event_dict


def _add_trace_context(
    _logger: object, _method: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Stamp ``trace_id`` / ``span_id`` when a recording span is current.

    Omitted entirely when there is no span, rather than logged as null.
    """
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return event_dict
    event_dict["trace_id"] = format(ctx.trace_id, "032x")
    event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


def resolve_log_format(
    configured: Literal["json", "console", "auto"],
) -> Literal["json", "console"]:
    """Explicit setting wins; otherwise a TTY gets console, a pipe gets JSON."""
    if configured != "auto":
        return configured
    return "console" if sys.stdout.isatty() else "json"


def setup_logging(
    *,
    level: str = "INFO",
    log_format: Literal["json", "console", "auto"] = "auto",
) -> None:
    resolved = resolve_log_format(log_format)
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _drop_noisy_keys,
        _redact_secrets,
        _add_trace_context,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer(ensure_ascii=False)
        if resolved == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    for name in _EXTERNAL_LOGGERS:
        logging.getLogger(name).propagate = True
    # One INFO line per outbound call duplicates our llm.call logs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # Our middleware writes the access line, with the request_id on it.
    logging.getLogger("uvicorn.access").disabled = True


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
