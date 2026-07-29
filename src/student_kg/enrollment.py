"""Phase 1 — structural initialization (enrollment).

Creates the minimal valid graph for a new student: a Student node plus a
single genesis InteractionEvent (type: ENROLLMENT). No Mastery nodes are
created here — mastery is a measurement derived from later interactions,
never an assumption made at registration time.

The genesis event has no predecessor (it IS the predecessor). Every later
event chains onto the previous one via :NEXT, and :LATEST_EVENT always
points at the newest event so appends don't have to walk the chain to
find where to attach.

Usage (needs the neo4j stack up: `make neo4j-up`):
    .venv/bin/python src/student_kg/enrollment.py --student-id s123 --name "Jane Doe" --cohort 2026A
"""

import argparse
import logging
import uuid

from neo4j import Driver

from driver import ensure_constraints, make_driver

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("enrollment")

_ENROLL_QUERY = """
CREATE (s:Student {
    id: $student_id,
    name: $name,
    cohort: $cohort,
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


def enroll_student(driver: Driver, student_id: str, name: str, cohort: str) -> str:
    event_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _ENROLL_QUERY,
            student_id=student_id,
            name=name,
            cohort=cohort,
            event_id=event_id,
        ).single()
    assert record, f"enrollment failed for student_id={student_id}"
    log.info("enrolled student_id=%s genesis_event_id=%s", record["student_id"], record["event_id"])
    return record["event_id"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--student-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--cohort", required=True)
    args = parser.parse_args()

    driver = make_driver()
    try:
        ensure_constraints(driver)
        enroll_student(driver, args.student_id, args.name, args.cohort)
    finally:
        driver.close()


if __name__ == "__main__":
    main()
