import re

from neo4j import Driver
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.errors import error_body
from src.student_kg.session import validate_session

# Exact paths reachable without a session token. Register/login issue the token in
# the first place, so they can't require one; health/docs are operational.
_OPEN_PATHS = {
    "/health",
    "/students/register",
    "/students/login",
    "/docs",
    "/docs/oauth2-redirect",
    "/openapi.json",
    "/redoc",
}

# Route handlers that take no auth dependency at all (pure catalog lookups or
# ungraded/no-graph-write grading) — see the docstrings on quiz.py/flashcards.py.
# Regexes, not prefixes: sibling routes under the same parent path (e.g.
# /quiz/questions/{uid}/log, /flashcards/cards/{uid}/log|history) DO require auth,
# so a plain startswith() would wrongly open them up too.
_OPEN_PATTERNS = tuple(
    re.compile(p)
    for p in (
        r"^/quiz/topics$",
        r"^/quiz/subjects$",
        r"^/quiz/questions$",
        r"^/quiz/topics/.+/questions$",
        r"^/quiz/questions/[^/]+/check$",
        r"^/flashcards/cards$",
        r"^/flashcards/topics/.+/cards$",
        r"^/flashcards/cards/[^/]+/reveal$",
    )
)


def _is_open_path(path: str) -> bool:
    if path in _OPEN_PATHS:
        return True
    return any(pattern.match(path) for pattern in _OPEN_PATTERNS)


class SessionAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, driver_factory) -> None:
        super().__init__(app)
        self._driver_factory = driver_factory

    async def dispatch(self, request: Request, call_next):
        if _is_open_path(request.url.path) or request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(error_body(401, "missing bearer token"), status_code=401)

        driver: Driver = self._driver_factory()
        student_id = validate_session(driver, token)
        if student_id is None:
            return JSONResponse(
                error_body(401, "invalid, expired, or revoked session"), status_code=401
            )

        request.state.student_id = student_id
        return await call_next(request)
