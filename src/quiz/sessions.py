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

A session is either scoped to one topic (topic_path given — every question in
questions_for_topic is fair game, not pinned to a fixed list) or, when topic_path is
None, a fixed-size due-first batch pinned at start_session() time exactly like
src.flashcards.sessions.start_session: due-for-review questions (soonest-due) fill the
batch first, then never-answered questions from the whole bank top up to `size`. The
topic-scoped path leaves question_uids empty/null since the frontend already fetches
the full topic list via GET /topics/{path}/questions and doesn't need a pinned batch.

Usage:
    from src.quiz.sessions import start_session, end_session, history_for_student
    session = start_session(driver, student_id, topic_path="CARDIOLOGY > ...")
    # ... record_attempt(..., session_id=session["session_id"]) for each question ...
    end_session(driver, student_id, session["session_id"])
    history_for_student(driver, student_id)
"""

import uuid
from datetime import datetime

from neo4j import Driver

from src.quiz.attempts import due_for_review
from src.quiz.bank import QuestionBank
from src.student_kg.energy import ENERGY_PER_QUIZ_SESSION

DEFAULT_BATCH_SESSION_SIZE = 10


def _select_question_uids(
    driver: Driver,
    student_id: str,
    bank: QuestionBank,
    size: int,
) -> list[str]:
    """Due questions first (soonest-due), then never-answered questions from the whole
    bank, capped at size. Mirrors src.flashcards.sessions._select_card_uids."""
    all_questions = bank.all()
    all_uids = {q.uid for q in all_questions}

    due = due_for_review(driver, student_id=student_id, limit=size)
    due_uids = [r["question_uid"] for r in due if r["question_uid"] in all_uids]

    remaining = size - len(due_uids)
    new_uids: list[str] = []
    if remaining > 0:
        seen = set(due_uids)
        for question in all_questions:
            if question.uid in seen:
                continue
            new_uids.append(question.uid)
            if len(new_uids) == remaining:
                break

    return due_uids + new_uids


_START_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})
OPTIONAL MATCH (s)-[latest:LATEST_EVENT]->(prev:InteractionEvent)
WITH s, prev, latest, CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END AS effective_ts
CREATE (sess:InteractionEvent:QuizSession {
    id: $session_id,
    type: "QUIZ_SESSION",
    topic_path: $topic_path,
    question_uids: $question_uids,
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
RETURN sess.id AS session_id, sess.question_uids AS question_uids
"""


def start_session(
    driver: Driver,
    student_id: str,
    topic_path: str | None = None,
    bank: QuestionBank | None = None,
    size: int = DEFAULT_BATCH_SESSION_SIZE,
    ts: datetime | None = None,
) -> dict | None:
    """Creates a QuizSession event and returns {"session_id", "question_uids"}.

    With topic_path given, scopes to that topic — question_uids is left empty since the
    frontend already fetches the topic's full question list separately; bank/size are
    unused in this path. Raises if student_id doesn't match a Student node (an
    already-validated topic_path is assumed, same as before).

    With topic_path None, picks a due-first batch of up to `size` questions across the
    whole bank (bank is required in this path) and pins it as question_uids, mirroring
    src.flashcards.sessions.start_session. Returns None if the bank has no questions at
    all.

    `ts` backdates started_at/ts for synthetic history generation (see
    scripts/generate_dummy_interactions.py) — real callers omit it and get datetime().
    """
    if topic_path is None:
        assert bank is not None, "bank is required for a cross-topic batch session"
        question_uids = _select_question_uids(
            driver, student_id=student_id, bank=bank, size=size
        )
        if not question_uids:
            return None
    else:
        question_uids = []

    session_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _START_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
            topic_path=topic_path,
            question_uids=question_uids,
            ts=ts.isoformat() if ts else None,
        ).single()
    assert record, f"session start failed — no student with id={student_id}"
    return {
        "session_id": record["session_id"],
        "question_uids": record["question_uids"],
    }


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
