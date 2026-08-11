"""OpenAPI documentation wiring — kept out of main.py/routers so route code stays
free of doc concerns, but generated live from the same FastAPI app on every request
to /openapi.json (cached after first build, same as FastAPI's default behavior).
Editing this file changes /docs and /redoc without touching route logic; editing
route signatures/docstrings/response_models still flows through automatically since
this wraps FastAPI's own get_openapi(), it doesn't replace it.
"""

from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

API_DESCRIPTION = """
Backend for a spaced-repetition tutoring app for medical students: quiz questions
and flashcards drawn from a Neo4j-backed knowledge graph, with per-student mastery
tracking, review scheduling, and light gamification (streaks, energy).

## Authentication

Every endpoint except `POST /students/register`, `POST /students/login`, `GET
/health`, and the docs routes requires a bearer session token:

```
Authorization: Bearer <token>
```

Tokens are issued by `/students/register` and `/students/login` and are validated
on every request by session-lookup middleware — an invalid, expired, or revoked
token gets a `401` before the request reaches the route handler, for every path not
in the exception list above.

## Errors

All error responses share one envelope, regardless of which handler produced them:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "human-readable message",
    "details": [ "...optional, e.g. field-level validation errors..." ]
  }
}
```

`code` is stable and machine-readable — branch on it. `message` is for humans and
may be reworded over time. `details` is only present on `422` validation errors
(FastAPI's per-field error list) and is otherwise omitted.

## Rate limiting

`POST /students/register` (5/minute) and `POST /students/login` (10/minute) are
rate-limited per client IP. Exceeding the limit returns `429` with `code:
RATE_LIMITED` in the standard error envelope.
"""

TAGS_METADATA: list[dict[str, Any]] = [
    {
        "name": "students",
        "description": (
            "Registration, login/session lifecycle, and per-student profile, streak, "
            "energy, and cross-content review nudges."
        ),
    },
    {
        "name": "quiz",
        "description": (
            "Topic/subject browsing, quiz sessions, answer grading, spaced-repetition "
            "review queues, and BKT mastery sync for multiple-choice questions."
        ),
    },
    {
        "name": "flashcards",
        "description": (
            "Flashcard browsing, Anki-style review sessions, per-card spaced-repetition "
            "scheduling, and review history."
        ),
    },
]

_ERROR_ENVELOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "error": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Stable, machine-readable error code to branch on.",
                    "example": "NOT_FOUND",
                },
                "message": {
                    "type": "string",
                    "description": "Human-readable message; wording may change.",
                    "example": "resource not found",
                },
                "details": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Present only on 422 validation errors.",
                },
            },
            "required": ["code", "message"],
        }
    },
    "required": ["error"],
}

# status -> (default code, example message), used to pre-populate a realistic
# example on every operation's error responses without hand-writing one per route.
_COMMON_ERROR_EXAMPLES: dict[int, tuple[str, str]] = {
    400: ("BAD_REQUEST", "selected_index out of range"),
    401: ("UNAUTHORIZED", "invalid, expired, or revoked session"),
    404: ("NOT_FOUND", "resource not found"),
    409: ("CONFLICT", "resource already exists or conflicts with existing data"),
    422: ("VALIDATION_ERROR", "invalid request"),
    429: ("RATE_LIMITED", "too many requests, try again later"),
    503: ("SERVICE_UNAVAILABLE", "service temporarily unavailable"),
}


def _build_error_example(status_code: int) -> dict[str, Any]:
    code, message = _COMMON_ERROR_EXAMPLES[status_code]
    example: dict[str, Any] = {"error": {"code": code, "message": message}}
    if status_code == 422:
        example["error"]["details"] = [
            {
                "type": "missing",
                "loc": ["body", "field_name"],
                "msg": "Field required",
            }
        ]
    return example


def custom_openapi(app: FastAPI) -> dict[str, Any]:
    """Drop-in replacement for `app.openapi`. Builds the schema via FastAPI's own
    get_openapi() (so response_models, docstrings, and Depends-based auth keep
    working as normal), then layers on: a detailed top-level description, tag
    descriptions/ordering, a reusable ErrorEnvelope schema, and realistic
    `error` examples on every documented non-2xx response. Result is cached on
    app.openapi_schema by FastAPI after the first call.
    """
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=API_DESCRIPTION,
        routes=app.routes,
        tags=TAGS_METADATA,
    )

    schema.setdefault("components", {}).setdefault("schemas", {})["ErrorEnvelope"] = (
        _ERROR_ENVELOPE_SCHEMA
    )

    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses", {})
            for status_code_str, response in responses.items():
                if not status_code_str.isdigit():
                    continue
                status_code = int(status_code_str)
                if status_code < 400 or status_code not in _COMMON_ERROR_EXAMPLES:
                    continue
                content = response.setdefault("content", {}).setdefault(
                    "application/json", {}
                )
                content["schema"] = {
                    "$ref": "#/components/schemas/ErrorEnvelope"
                }
                content["example"] = _build_error_example(status_code)

    app.openapi_schema = schema
    return app.openapi_schema


def install_openapi(app: FastAPI) -> None:
    """Wire the custom schema builder into the app. Call once at startup."""
    app.openapi = lambda: custom_openapi(app)


def error_responses(*status_codes: int) -> dict[int, dict[str, Any]]:
    """Build a `responses=` dict for a route decorator from a list of status codes
    the handler can actually raise (e.g. `error_responses(401, 404)`). Keeps routers
    free of response-body/example detail — they only declare *which* codes apply,
    this module supplies the shared description; `custom_openapi()` fills in the
    ErrorEnvelope schema + example afterwards from `_COMMON_ERROR_EXAMPLES`.
    """
    descriptions = {
        400: "Bad request — request data failed a semantic check.",
        401: "Missing, invalid, expired, or revoked bearer session token.",
        404: "No such resource for this student.",
        409: "Conflict with existing data or current state.",
        429: "Rate limit exceeded.",
        503: "Backing service temporarily unavailable.",
    }
    return {
        code: {"description": descriptions.get(code, "Error.")} for code in status_codes
    }
