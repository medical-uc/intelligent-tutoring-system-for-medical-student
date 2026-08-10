"""Per-topic mastery — a Bayesian Knowledge Tracing p_know estimate, stored as a
Student-[:MASTERS]->Topic edge (properties: p_know, updated_at) on the same leaf Topic
node :BELONGS_TO already targets (see src/quiz/attempts.py).

Unlike the reverted src/quiz/mastery.py (see git history: f3574f2 / 2aef2a1), BKT is not
computed server-side here — the frontend runs its own BKT update on-device after a quiz
session and pushes the resulting p_know per topic. This module is a thin, deliberately
dumb persistence layer: it MERGEs whatever p_know the client sends onto the :MASTERS edge,
with no recomputation, no clamping, and no server-side knowledge of BKT params. The
client is the single source of truth for the algorithm; the server just remembers the
latest value per student+topic so it survives across devices/sessions.

Usage:
    from src.quiz.mastery import upsert_mastery, mastery_for_student
    upsert_mastery(driver, student_id, items=[{"topic_path": "...", "p_know": 0.74}])
    mastery_for_student(driver, student_id)
"""

from datetime import datetime

from neo4j import Driver

_UPSERT_MASTERY_QUERY = """
UNWIND $items AS item
MATCH (s:Student {id: $student_id})
MERGE (t:Topic {path: item.topic_path})
MERGE (s)-[m:MASTERS]->(t)
SET m.p_know = item.p_know,
    m.updated_at = CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END
RETURN count(m) AS updated_count
"""


def upsert_mastery(
    driver: Driver,
    student_id: str,
    items: list[dict],
    ts: datetime | None = None,
) -> int | None:
    """Overwrites p_know (and updated_at) on this student's :MASTERS edge for each
    {topic_path, p_know} in items, one MERGE per topic — creating the Topic node and/or
    the edge if either doesn't exist yet. Last-write-wins per topic_path if items repeats
    one; no ordering/versioning is enforced since the client already resolved that on
    device. Returns the number of edges written, or None if student_id doesn't match a
    Student node.

    `ts` backdates updated_at for synthetic history generation — real callers omit it and
    get datetime()."""
    with driver.session() as session:
        record = session.run(
            _UPSERT_MASTERY_QUERY,
            student_id=student_id,
            items=items,
            ts=ts.isoformat() if ts else None,
        ).single()
    if record is None or record["updated_count"] == 0:
        return None
    return record["updated_count"]


_MASTERY_FOR_STUDENT_QUERY = """
MATCH (s:Student {id: $student_id})-[m:MASTERS]->(t:Topic)
RETURN t.path AS topic_path, m.p_know AS p_know, m.updated_at AS updated_at
ORDER BY m.p_know ASC
"""


def mastery_for_student(driver: Driver, student_id: str) -> list[dict]:
    """Returns this student's current p_know per leaf topic, weakest topic first. A topic
    never pushed by the client has no :MASTERS edge and so never appears here."""
    with driver.session() as session:
        records = session.run(_MASTERY_FOR_STUDENT_QUERY, student_id=student_id)
        return [dict(r) for r in records]
