"""Phase 1 — structural initialization (enrollment).

Creates the minimal valid graph for a new student: a Student node plus a
single genesis InteractionEvent (type: ENROLLMENT). No Mastery nodes are
created here — mastery is a measurement derived from later interactions,
never an assumption made at registration time.

The genesis event has no predecessor (it IS the predecessor). Every later
event chains onto the previous one via :NEXT, and :LATEST_EVENT always
points at the newest event so appends don't have to walk the chain to
find where to attach.

Usage (needs the neo4j stack up: `make neo4j-up`; run from repo root):
    .venv/bin/python -m src.student_kg.enrollment --full-name "Jane Doe" --student-number "21-2023-045" --academic-year 3
"""

import argparse
import logging
import uuid

from neo4j import Driver

from src.student_kg.driver import ensure_constraints, make_driver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("enrollment")

_ENROLL_QUERY = """
CREATE (s:Student {
    id: $student_id,
    full_name: $full_name,
    student_number: $student_number,
    academic_year: $academic_year,
    enrolled_at: datetime()
})
CREATE (e:InteractionEvent {
    id: $event_id,
    type: "ENROLLMENT",
    ts: datetime()
})
CREATE (s)-[:HAS_EVENT]->(e)
CREATE (s)-[:LATEST_EVENT]->(e)
RETURN s.id AS student_id, e.id AS event_id
"""


def enroll_student(driver: Driver, full_name: str, student_number: str, academic_year: int) -> tuple[str, str]:
    assert 1 <= academic_year <= 6, f"academic_year must be 1-6, got {academic_year}"
    student_id = str(uuid.uuid4())
    event_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _ENROLL_QUERY,
            student_id=student_id,
            full_name=full_name,
            student_number=student_number,
            academic_year=academic_year,
            event_id=event_id,
        ).single()
    assert record, f"enrollment failed for student_id={student_id}"
    log.info("enrolled student_id=%s genesis_event_id=%s", record["student_id"], record["event_id"])
    return record["student_id"], record["event_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
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
