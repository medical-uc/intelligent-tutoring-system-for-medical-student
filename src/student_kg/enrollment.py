"""Phase 1 — structural initialization (enrollment).

Creates the minimal valid graph for a new student: just the Student node. No Mastery
nodes are created here — mastery is a measurement derived from later interactions, never
an assumption made at registration time. No genesis event either — nothing in the system
reads an ENROLLMENT event, and the :NEXT/:LATEST_EVENT chain (see src/quiz/sessions.py)
tolerates starting from an empty chain (OPTIONAL MATCH on the first-ever event finds
nothing, so the first QuizSession simply has no predecessor).

Usage (needs the neo4j stack up: `make neo4j-up`; run from repo root):
    .venv/bin/python -m src.student_kg.enrollment --full-name "Jane Doe" --student-number "21-2023-045" --academic-year 3
"""

import argparse
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from neo4j import Driver

from src.student_kg.driver import ensure_constraints, make_driver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("enrollment")

_ENROLL_QUERY = """
CREATE (s:Student {
    id: $student_id,
    full_name: $full_name,
    student_number: $student_number,
    academic_year: $academic_year,
    enrolled_at: datetime(),
    energy: 0
})
RETURN s.id AS student_id
"""


def enroll_student(
    driver: Driver, full_name: str, student_number: str, academic_year: int
) -> str:
    assert 1 <= academic_year <= 6, f"academic_year must be 1-6, got {academic_year}"
    student_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _ENROLL_QUERY,
            student_id=student_id,
            full_name=full_name,
            student_number=student_number,
            academic_year=academic_year,
        ).single()
    assert record, f"enrollment failed for student_id={student_id}"
    log.info("enrolled student_id=%s", record["student_id"])
    return record["student_id"]


@dataclass
class StudentProfile:
    student_id: str
    full_name: str
    student_number: str
    academic_year: int
    enrolled_at: datetime


_GET_STUDENT_QUERY = """
MATCH (s:Student {id: $student_id})
RETURN s.id AS student_id,
       s.full_name AS full_name,
       s.student_number AS student_number,
       s.academic_year AS academic_year,
       s.enrolled_at AS enrolled_at
"""


def get_student_by_id(driver: Driver, student_id: str) -> StudentProfile | None:
    with driver.session() as session:
        record = session.run(_GET_STUDENT_QUERY, student_id=student_id).single()
    if record is None:
        return None
    return StudentProfile(
        student_id=record["student_id"],
        full_name=record["full_name"],
        student_number=record["student_number"],
        academic_year=record["academic_year"],
        enrolled_at=record["enrolled_at"].to_native(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--full-name", required=True)
    parser.add_argument("--student-number", required=True)
    parser.add_argument("--academic-year", type=int, required=True, choices=range(1, 7))
    args = parser.parse_args()

    driver = make_driver()
    try:
        ensure_constraints(driver)
        enroll_student(driver, args.full_name, args.student_number, args.academic_year)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
