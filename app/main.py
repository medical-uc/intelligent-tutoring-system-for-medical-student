"""Registration API entrypoint.

Usage (needs the neo4j stack up: `make neo4j-up`; run from repo root):
    .venv/bin/uvicorn app.main:app --reload
"""

from dotenv import load_dotenv
from fastapi import FastAPI

from app.routers import flashcards, quiz, students

load_dotenv()

app = FastAPI(title="Student Knowledge Graph API")
app.include_router(students.router)
app.include_router(quiz.router)
app.include_router(flashcards.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
