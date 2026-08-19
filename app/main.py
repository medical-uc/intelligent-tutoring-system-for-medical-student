"""Registration API entrypoint.

Usage (needs the neo4j stack up: `make neo4j-up`; run from repo root):
    .venv/bin/uvicorn app.main:app --reload
"""

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from starlette.responses import JSONResponse

from app.auth_middleware import SessionAuthMiddleware
from app.dependencies import get_driver
from app.errors import error_body, register_exception_handlers
from app.limiter import limiter
from app.openapi import install_openapi
from app.routers import flashcards, quiz, students, subjects

load_dotenv()

app = FastAPI(title="Student Knowledge Graph API", version="0.1.0")
app.state.limiter = limiter
install_openapi(app)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content=error_body(429, "too many requests, try again later"),
    )


_allowed_origins = [
    origin.strip()
    for origin in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(SessionAuthMiddleware, driver_factory=get_driver)

register_exception_handlers(app)

app.include_router(students.router)
app.include_router(quiz.router)
app.include_router(subjects.router)
app.include_router(flashcards.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
