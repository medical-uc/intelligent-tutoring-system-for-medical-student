from neo4j import Driver
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.student_kg.session import validate_session

# Paths reachable without a session token. Register/login issue the token in the
# first place, so they can't require one; health/docs are operational.
_OPEN_PATHS = {
    "/health",
    "/students/register",
    "/students/login",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class SessionAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, driver_factory) -> None:
        super().__init__(app)
        self._driver_factory = driver_factory

    async def dispatch(self, request: Request, call_next):
        if request.url.path in _OPEN_PATHS or request.method == "OPTIONS":
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(
                {"detail": "missing bearer token"}, status_code=401
            )

        driver: Driver = self._driver_factory()
        student_id = validate_session(driver, token)
        if student_id is None:
            return JSONResponse(
                {"detail": "invalid, expired, or revoked session"}, status_code=401
            )

        request.state.student_id = student_id
        return await call_next(request)
