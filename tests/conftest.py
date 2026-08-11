"""Shared fixtures for API integration tests.

These hit the real Neo4j + Postgres stack (`make neo4j-up`, `make populate` must have
run at least once so the question bank and domain graph are populated). Every fixture
that creates a Student cleans up exactly what it created — Session/QuizSession/
FlashcardSession/InteractionEvent nodes are student-owned and safe to delete, but
Topic/Question/Flashcard nodes are shared across the whole graph and must never be
touched by test cleanup.
"""

import uuid

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

load_dotenv()

from app.limiter import limiter  # noqa: E402
from app.main import app  # noqa: E402
from src.quiz.bank import load_question_bank  # noqa: E402
from src.student_kg.driver import ensure_constraints, make_driver  # noqa: E402

_DELETE_STUDENT_QUERY = """
MATCH (s:Student {id: $student_id})
OPTIONAL MATCH (s)-[:HAS_SESSION]->(sess:Session)
OPTIONAL MATCH (s)-[:ATTEMPTED]->(run)
OPTIONAL MATCH (run)-[:HAS_ANSWER]->(event:InteractionEvent)
DETACH DELETE s, sess, run, event
"""


@pytest.fixture(scope="session")
def driver():
    d = make_driver()
    ensure_constraints(d)
    yield d
    d.close()


def cleanup_student(driver, student_id: str) -> None:
    """Deletes a Student and everything it owns (Session/QuizSession/FlashcardSession/
    InteractionEvent) — never Topic/Question/Flashcard, which are shared across the
    whole graph. The one cleanup path every test should use instead of hand-rolling
    `DETACH DELETE s` (which only drops the Student's own relationships, leaving any
    Session node it pointed to orphaned since Session has no other owner)."""
    with driver.session() as session:
        session.run(_DELETE_STUDENT_QUERY, student_id=student_id)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    limiter.reset()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _unique_student_number() -> str:
    return f"test-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def register_student(client, driver):
    """Factory fixture: call to register a fresh student, get back (student_id, token,
    headers). Registers with a random student_number so parallel/repeat runs never
    collide, and deletes the student (and only its own Session/QuizSession/
    FlashcardSession/InteractionEvent nodes) on teardown."""
    created_ids = []

    def _register(full_name: str = "Test Student", academic_year: int = 3) -> dict:
        student_number = _unique_student_number()
        body = {
            "full_name": full_name,
            "student_number": student_number,
            "academic_year": academic_year,
        }
        resp = client.post("/students/register", json=body)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        created_ids.append(data["student_id"])
        return {
            "student_id": data["student_id"],
            "student_number": student_number,
            "token": data["token"],
            "headers": {"Authorization": f"Bearer {data['token']}"},
        }

    yield _register

    for student_id in created_ids:
        cleanup_student(driver, student_id)


@pytest.fixture
def student(register_student):
    """A single ready-to-use registered student — the common case."""
    return register_student()


@pytest.fixture(scope="session")
def bank():
    return load_question_bank()


@pytest.fixture(scope="session")
def sample_question(bank):
    """A real question with >=2 options, so selected_index has room to be wrong too."""
    for q in bank.all():
        if len(q.options) >= 2:
            return q
    pytest.skip("question bank has no question with >=2 options")


@pytest.fixture(scope="session")
def sample_topic(sample_question):
    return sample_question.topic_path
