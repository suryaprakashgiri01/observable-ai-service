"""OpenAPI documentation shared by every router: common headers and the error envelope.

Nothing here changes behaviour. The headers below are read by middleware, so
without declaring them on the routes they would be invisible in Swagger.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Header
from pydantic import BaseModel, Field

from app.exceptions.codes import STATUS_BY_CODE, ErrorCode


async def document_common_headers(
    x_request_id: Annotated[
        str | None,
        Header(
            alias="X-Request-ID",
            description=(
                "Optional correlation id, echoed in the `X-Request-ID` response header and "
                "on every log line of the request. `[A-Za-z0-9._:-]{1,128}`; anything else "
                "is replaced by a generated id. Omit it to get one generated."
            ),
            openapi_examples={
                "none": {"summary": "Generate one", "value": ""},
                "demo": {"summary": "Fixed id for grepping logs", "value": "demo-1"},
            },
        ),
    ] = None,
    traceparent: Annotated[
        str | None,
        Header(
            description=(
                "Optional W3C trace context. When present, this request joins the caller's "
                "trace and the response `trace_id` is the caller's."
            ),
            openapi_examples={
                "none": {"summary": "Start a new trace", "value": ""},
                "continue": {
                    "summary": "Continue an upstream trace",
                    "value": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
                },
            },
        ),
    ] = None,
) -> None:
    """Documentation only: the middleware reads both headers from the raw request."""


class ErrorBody(BaseModel):
    code: ErrorCode = Field(description="Stable, machine-readable error code.")
    message: str = Field(description="Human-readable explanation.")
    request_id: str | None = Field(description="Search the logs for this id.")
    details: list[dict[str, Any]] | None = Field(
        default=None,
        description="Validation errors only: one entry per field with `loc`, `msg`, `type`.",
    )


class ErrorResponse(BaseModel):
    error: ErrorBody


_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.VALIDATION_ERROR: "Request validation failed",
    ErrorCode.AUTH_REQUIRED: "X-API-Key header is required",
    ErrorCode.AUTH_INVALID: "Invalid API key",
    ErrorCode.FORBIDDEN: "Admin role required",
    ErrorCode.MODEL_NOT_FOUND: "Unknown model 'nope'",
    ErrorCode.MODEL_UNAVAILABLE: (
        "Model 'claude-opus-5-5' is not available: its provider has no credentials"
    ),
    ErrorCode.MODEL_CONFLICT: "Model id 'gpt-4.1' is already taken",
    ErrorCode.MODEL_LIMIT_REACHED: "At most 10 custom models per user",
    ErrorCode.ENCRYPTION_NOT_CONFIGURED: "APP_ENCRYPTION_KEY is not set; cannot store an API key",
    ErrorCode.TICKET_NOT_FOUND: "Ticket 999999 not found",
    ErrorCode.NOT_FOUND: "Not Found",
    ErrorCode.TOOL_FAILED: "Tool 'create_ticket' failed",
    ErrorCode.LLM_UNAVAILABLE: "No model in the fallback chain answered",
    ErrorCode.INTERNAL_ERROR: "Internal server error",
}

_DETAILS: dict[ErrorCode, list[dict[str, Any]]] = {
    ErrorCode.VALIDATION_ERROR: [
        {
            "loc": ["body", "question"],
            "msg": "String should have at least 1 character",
            "type": "string_too_short",
        }
    ],
}

_DESCRIPTIONS: dict[int, str] = {
    400: "Bad request: the named model cannot be used",
    401: "Missing or invalid `X-API-Key`",
    403: "Forbidden",
    404: "Not found",
    409: "Conflict",
    422: "Validation error: see `error.details`",
    500: "Unexpected error: details are only in the logs for `request_id`",
    502: "A business action (tool) failed",
    503: "No model in the fallback chain answered",
}


def _example(code: ErrorCode) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": code.value,
        "message": _MESSAGES[code],
        "request_id": "demo-1",
    }
    if code in _DETAILS:
        body["details"] = _DETAILS[code]
    return {"summary": code.value, "value": {"error": body}}


def error_responses(*codes: ErrorCode) -> dict[int | str, dict[str, Any]]:
    """`responses=` for a route: the envelope schema plus one example per code, by status."""
    by_status: dict[int, list[ErrorCode]] = {}
    for code in (*codes, ErrorCode.INTERNAL_ERROR):
        by_status.setdefault(STATUS_BY_CODE[code], []).append(code)
    return {
        status: {
            "model": ErrorResponse,
            "description": _DESCRIPTIONS.get(status, "Error"),
            "content": {"application/json": {"examples": {c.value: _example(c) for c in group}}},
        }
        for status, group in sorted(by_status.items())
    }


AUTH_ERRORS = (ErrorCode.AUTH_REQUIRED, ErrorCode.AUTH_INVALID, ErrorCode.VALIDATION_ERROR)
