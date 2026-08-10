"""Quiz sessions — the unit shown in the student-facing history view.

A QuizSession groups a run of QUIZ_ANSWER InteractionEvents (one topic, one sitting) so
the app can show "Cardiology Quiz, 10 questions, 12m, 80%" as a single row instead of
individual question attempts. This mirrors the existing InteractionEvent chain pattern
(see src/student_kg/enrollment.py) rather than inventing a new relationship style:
QuizSession is itself an InteractionEvent (type: QUIZ_SESSION) linked to the student via
:ATTEMPTED (plus the shared :NEXT/:LATEST_EVENT timeline chain other InteractionEvent types
use :HAS_EVENT for), and individual answers link to their session via a separate
:HAS_ANSWER edge.

Sessions are explicit (start/end calls), not inferred from time gaps between answers —
the frontend starts a session before the first question and ends it after the last, so
grouping is exact rather than heuristic. See src/quiz/attempts.py::record_attempt, which
takes the session_id returned by start_session().

Usage:
    from src.quiz.sessions import start_session, end_session, history_for_student
    session_id = start_session(driver, student_id, topic_path="CARDIOLOGY > ...")
    # ... record_attempt(..., session_id=session_id) for each question ...
    end_session(driver, student_id, session_id)
    history_for_student(driver, student_id)
"""

import uuid
from datetime import datetime

from neo4j import Driver

from src.student_kg.energy import ENERGY_PER_QUIZ_SESSION

_START_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})
OPTIONAL MATCH (s)-[latest:LATEST_EVENT]->(prev:InteractionEvent)
WITH s, prev, latest, CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END AS effective_ts
CREATE (sess:InteractionEvent:QuizSession {
    id: $session_id,
    type: "QUIZ_SESSION",
    topic_path: $topic_path,
    status: "in_progress",
    started_at: effective_ts,
    ended_at: null,
    question_count: null,
    correct_count: null,
    duration_seconds: null,
    ts: effective_ts
})
CREATE (s)-[:ATTEMPTED]->(sess)
FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END | CREATE (prev)-[:NEXT]->(sess))
DELETE latest
CREATE (s)-[:LATEST_EVENT]->(sess)
RETURN sess.id AS session_id
"""


def start_session(
    driver: Driver, student_id: str, topic_path: str, ts: datetime | None = None
) -> str:
    """Creates a QuizSession event and returns its id. Raises if student_id doesn't match
    a Student node.

    `ts` backdates started_at/ts for synthetic history generation (see
    scripts/generate_dummy_interactions.py) — real callers omit it and get datetime().
    """
    session_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _START_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
            topic_path=topic_path,
            ts=ts.isoformat() if ts else None,
        ).single()
    assert record, f"session start failed — no student with id={student_id}"
    return record["session_id"]


_END_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(sess:QuizSession {id: $session_id})
OPTIONAL MATCH (sess)-[:HAS_ANSWER]->(a:InteractionEvent {type: "QUIZ_ANSWER"})
WITH s, sess, count(a) AS question_count, sum(CASE WHEN a.correct THEN 1 ELSE 0 END) AS correct_count,
     CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END AS effective_ts
SET sess.status = "completed",
    sess.ended_at = effective_ts,
    sess.question_count = question_count,
    sess.correct_count = correct_count,
    sess.duration_seconds = duration.inSeconds(sess.started_at, effective_ts).seconds,
    s.energy = coalesce(s.energy, 0) + $energy_award
RETURN sess.id AS session_id, sess.question_count AS question_count,
       sess.correct_count AS correct_count, sess.duration_seconds AS duration_seconds,
       $energy_award AS energy_awarded, s.energy AS energy_balance
"""


def end_session(
    driver: Driver, student_id: str, session_id: str, ts: datetime | None = None
) -> dict | None:
    """Finalizes a session: aggregates its linked answers into question_count/correct_count/
    duration_seconds, marks it completed, and awards ENERGY_PER_QUIZ_SESSION energy (see
    src.student_kg.energy) — flat, not scaled to score. Returns the summary dict (including
    energy_awarded/energy_balance), or None if no such in-progress session exists for this
    student.

    `ts` backdates ended_at (and therefore duration_seconds, computed against the session's
    real/backdated started_at) for synthetic history generation — real callers omit it.
    """
    with driver.session() as session:
        record = session.run(
            _END_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
            ts=ts.isoformat() if ts else None,
            energy_award=ENERGY_PER_QUIZ_SESSION,
        ).single()
    return dict(record) if record else None


_CANCEL_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(sess:QuizSession {id: $session_id, status: "in_progress"})
OPTIONAL MATCH (sess)-[:HAS_ANSWER]->(a:InteractionEvent {type: "QUIZ_ANSWER"})
WITH s, sess, count(a) AS question_count, sum(CASE WHEN a.correct THEN 1 ELSE 0 END) AS correct_count
SET sess.status = "cancelled",
    sess.ended_at = datetime(),
    sess.question_count = question_count,
    sess.correct_count = correct_count,
    sess.duration_seconds = duration.inSeconds(sess.started_at, datetime()).seconds
RETURN sess.id AS session_id, sess.question_count AS question_count,
       sess.correct_count AS correct_count, sess.duration_seconds AS duration_seconds
"""


def cancel_session(driver: Driver, student_id: str, session_id: str) -> dict | None:
    """Ends a session early, same aggregation as end_session but status "cancelled" instead
    of "completed". Answers already logged via record_attempt() before the cancel stand as-is
    — their :REVIEWING mastery updates already fired per-question and are not unwound; only
    the session's own status/counts change. Returns the summary dict, or None if no such
    in-progress session exists for this student — the match requires status "in_progress",
    so cancelling an already-completed or already-cancelled session is a no-op (returns None)
    rather than silently overwriting its real ended_at/duration_seconds."""
    with driver.session() as session:
        record = session.run(
            _CANCEL_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
        ).single()
    return dict(record) if record else None


_HISTORY_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(sess:QuizSession)
WHERE sess.status IN ["completed", "cancelled"]
RETURN sess.id AS session_id, sess.topic_path AS topic_path, sess.status AS status,
       sess.question_count AS question_count, sess.correct_count AS correct_count,
       sess.duration_seconds AS duration_seconds, sess.started_at AS started_at,
       sess.ended_at AS ended_at
ORDER BY sess.started_at DESC
LIMIT $limit
"""


def history_for_student(
    driver: Driver, student_id: str, limit: int = 100
) -> list[dict]:
    """Returns completed quiz sessions for a student, most recent first. Score/duration/day
    grouping is derived by the caller from these raw fields (see app/routers/quiz.py).
    """
    with driver.session() as session:
        records = session.run(
            _HISTORY_QUERY,
            student_id=student_id,
            limit=limit,
        )
        return [dict(r) for r in records]
