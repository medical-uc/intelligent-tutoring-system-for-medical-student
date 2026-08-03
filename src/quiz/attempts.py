"""Records quiz answer attempts as InteractionEvents on the student graph.

Each attempt is an InteractionEvent (type: QUIZ_ANSWER) linked only to its QuizSession via
:HAS_ANSWER — not to the student's top-level :HAS_EVENT/:NEXT timeline chain. Every
attempt already belongs to exactly one session (session_id is required), so the session
hop is never lossy, and skipping the direct Student edge avoids extending the chain-walk
timeline (meant for session/enrollment-level events) down to every single question. The
question bank itself (src.quiz.bank) stays the source of truth for question content — only
the grading result is persisted here.

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
                   correct=True, confidence="confident", time_taken_seconds=12.4,
                   topic_tag=["ENDOCRINOLOGY", "FUNCTIONS OF HORMONE"])
"""

import uuid

from neo4j import Driver

_RECORD_ATTEMPT_QUERY = """
MATCH (s:Student {id: $student_id})
MATCH (s)-[:HAS_EVENT]->(sess:QuizSession {id: $session_id})
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
CREATE (sess)-[:HAS_ANSWER]->(e)
MERGE (q:Question {uid: $question_uid})
CREATE (e)-[:FOR_QUESTION]->(q)
WITH e, q
CALL (q) {
    UNWIND range(0, size($topic_tag) - 1) AS i
    WITH q, i, reduce(p = "", j IN range(0, i) | p + CASE WHEN j = 0 THEN "" ELSE " > " END + $topic_tag[j]) AS topic_path
    MERGE (t:Topic {path: topic_path})
    ON CREATE SET t.name = $topic_tag[i]
    WITH q, i, t
    ORDER BY i DESC
    WITH q, collect(t) AS topics
    CALL (topics) {
        UNWIND range(0, size(topics) - 2) AS j
        WITH topics[j] AS child, topics[j + 1] AS parent
        MERGE (child)-[:SUB_TOPIC_OF]->(parent)
    }
    WITH q, topics
    UNWIND topics[0..1] AS leaf
    MERGE (q)-[:BELONGS_TO]->(leaf)
}
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
    topic_tag: list[str],
) -> str | None:
    """Persists one fully-graded attempt (answer + confidence + timing together), linked to
    its QuizSession, and returns the new event id. Returns None if session_id doesn't match
    a session belonging to this student — a client-reachable condition (stale/foreign
    session id), not a server precondition failure, so the caller should turn this into a
    404 rather than treating it as an assertion violation.

    Also merges a Question node (deduped by uid across every student/session that has ever
    answered it) and a chain of nested Topic nodes from topic_tag (e.g.
    ["ENDOCRINOLOGY", "FUNCTIONS OF HORMONE"] becomes two Topic nodes linked by
    :SUB_TOPIC_OF), so mastery can be aggregated per-question or rolled up to any topic
    level instead of living only as a flat string property on the attempt."""
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
            topic_tag=topic_tag,
        ).single()
    return record["event_id"] if record else None


_TOPIC_PROGRESS_QUERY = """
MATCH (s:Student {id: $student_id})-[:HAS_EVENT]->(:QuizSession)-[:HAS_ANSWER]->(e:InteractionEvent {type: "QUIZ_ANSWER"})
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
