"""Central exception handling — nothing internal (Cypher, stack traces, driver
messages) should ever reach the client. Handlers log full detail server-side and
return one consistent envelope: {"error": {"code", "message", "details"}}.

`code` is the stable, machine-readable field frontend/mobile clients should branch
on; `message` is human-readable and may change wording over time.
"""

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from neo4j.exceptions import ConstraintError, Neo4jError, ServiceUnavailable

log = logging.getLogger("api.errors")

_CODE_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_422_UNPROCESSABLE_ENTITY: "VALIDATION_ERROR",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_ERROR",
    status.HTTP_503_SERVICE_UNAVAILABLE: "SERVICE_UNAVAILABLE",
}


def error_body(
    status_code: int, message: str, *, code: str | None = None, details: list | None = None
) -> dict:
    body = {
        "error": {
            "code": code or _CODE_BY_STATUS.get(status_code, "ERROR"),
            "message": message,
        }
    }
    if details is not None:
        body["error"]["details"] = details
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.status_code, str(exc.detail)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=error_body(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid request",
                details=exc.errors(),
            ),
        )

    @app.exception_handler(ConstraintError)
    async def constraint_error_handler(
        request: Request, exc: ConstraintError
    ) -> JSONResponse:
        log.warning("constraint violation on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=error_body(
                status.HTTP_409_CONFLICT,
                "resource already exists or conflicts with existing data",
            ),
        )

    @app.exception_handler(ServiceUnavailable)
    async def service_unavailable_handler(
        request: Request, exc: ServiceUnavailable
    ) -> JSONResponse:
        log.error("database unavailable on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=error_body(
                status.HTTP_503_SERVICE_UNAVAILABLE, "service temporarily unavailable"
            ),
        )

    @app.exception_handler(Neo4jError)
    async def neo4j_error_handler(request: Request, exc: Neo4jError) -> JSONResponse:
        log.error("database error on %s: %s", request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal server error"),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s", request.url.path)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=error_body(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal server error"),
        )
