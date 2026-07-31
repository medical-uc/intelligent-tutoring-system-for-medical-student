"""Records quiz answer attempts as InteractionEvents on the student graph.

Follows the same event-chain shape as enrollment's genesis event: every attempt is an
InteractionEvent (type: QUIZ_ANSWER) linked to the student via :HAS_EVENT, and
:LATEST_EVENT is repointed so the chain can be walked without a full scan. The question
bank itself (src.quiz.bank) stays the source of truth for question content — only the
grading result is persisted here.

Confidence (guessing/unsure/confident) and time_taken_seconds are captured on the same
event as correctness, because a correct guess and a confident correct answer are not the
same evidence of mastery, and a confident wrong answer is a stronger signal of a
misconception than an unsure wrong answer. Time taken adds a third axis: fast+correct is
solid mastery, slow+correct is fragile/effortful knowledge, fast+wrong is often a careless
slip or a confident misconception, slow+wrong is a genuine gap.

Grading itself is a separate, pure operation (see app/routers/quiz.py's /check endpoint,
or Question.correct_option_index() in src/quiz/bank.py) — it does no graph write and needs
neither confidence nor timing, so the frontend can show instant right/wrong feedback.
record_attempt() is the one write for the whole attempt, called only once selected_index,
confidence, and time_taken_seconds are all known, so exactly one complete InteractionEvent
is ever created — never a partial one.

Every attempt belongs to a QuizSession (see src/quiz/sessions.py) — the frontend starts a
session before the first question in a topic run and passes its id to every
record_attempt() call, which links the answer to that session via :HAS_ANSWER. This is
what lets the history view group answers back into "10 questions, 12m, 80%" rows instead
of a flat list of individual answers.

Usage:
    from src.quiz.attempts import record_attempt
    record_attempt(driver, student_id, session_id="...", question_uid="...", selected_index=0,
                   correct=True, confidence="confident", time_taken_seconds=12.4)
"""

import uuid

from neo4j import Driver

_RECORD_ATTEMPT_QUERY = """
MATCH (s:Student {id: $student_id})
MATCH (s)-[:HAS_EVENT]->(sess:QuizSession {id: $session_id})
OPTIONAL MATCH (s)-[latest:LATEST_EVENT]->(prev:InteractionEvent)
CREATE (e:InteractionEvent {
    id: $event_id,
    type: "QUIZ_ANSWER",
    question_uid: $question_uid,
    selected_index: $selected_index,
    correct: $correct,
    confidence: $confidence,
    time_taken_seconds: $time_taken_seconds,
    ts: datetime()
})
CREATE (s)-[:HAS_EVENT]->(e)
CREATE (sess)-[:HAS_ANSWER]->(e)
FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END | CREATE (prev)-[:NEXT]->(e))
DELETE latest
CREATE (s)-[:LATEST_EVENT]->(e)
RETURN e.id AS event_id
"""


def record_attempt(
    driver: Driver,
    student_id: str,
    session_id: str,
    question_uid: str,
    selected_index: int,
    correct: bool,
    confidence: str,
    time_taken_seconds: float,
) -> str | None:
    """Persists one fully-graded attempt (answer + confidence + timing together), linked to
    its QuizSession, and returns the new event id. Returns None if session_id doesn't match
    a session belonging to this student — a client-reachable condition (stale/foreign
    session id), not a server precondition failure, so the caller should turn this into a
    404 rather than treating it as an assertion violation."""
    event_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _RECORD_ATTEMPT_QUERY,
            student_id=student_id,
            session_id=session_id,
            event_id=event_id,
            question_uid=question_uid,
            selected_index=selected_index,
            correct=correct,
            confidence=confidence,
            time_taken_seconds=time_taken_seconds,
        ).single()
    return record["event_id"] if record else None


_TOPIC_PROGRESS_QUERY = """
MATCH (s:Student {id: $student_id})-[:HAS_EVENT]->(e:InteractionEvent {type: "QUIZ_ANSWER"})
WHERE e.question_uid IN $question_uids
RETURN e.question_uid AS question_uid, e.correct AS correct, e.confidence AS confidence,
       e.time_taken_seconds AS time_taken_seconds, e.ts AS ts
ORDER BY e.ts ASC
"""


def attempts_for_questions(driver: Driver, student_id: str, question_uids: list[str]) -> list[dict]:
    """Returns every attempt this student made on the given question uids, oldest first."""
    with driver.session() as session:
        records = session.run(
            _TOPIC_PROGRESS_QUERY,
            student_id=student_id,
            question_uids=question_uids,
        )
        return [dict(r) for r in records]
