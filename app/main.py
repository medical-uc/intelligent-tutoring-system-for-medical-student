"""Registration API entrypoint.

Usage (needs the neo4j stack up: `make neo4j-up`; run from repo root):
    .venv/bin/uvicorn app.main:app --reload
"""

from fastapi import FastAPI

from app.routers import students

app = FastAPI(title="Student Knowledge Graph API")
app.include_router(students.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
