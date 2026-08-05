"""Records quiz answer attempts as InteractionEvents on the student graph.

Each attempt is an InteractionEvent (type: QUIZ_ANSWER) linked only to its QuizSession via
:HAS_ANSWER — not to the student's top-level :ATTEMPTED/:NEXT timeline chain. Every
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

record_attempt() also updates a :REVIEWING edge from Student straight to Question, holding
simple spaced-repetition state (streak, interval_days, next_review_at). Only a correct AND
confident answer counts as a "strong pass" that grows the streak and doubles the interval
(1 -> 2 -> 4 -> 8... days, capped at 60) — a correct guess or an unsure-but-right answer is
not real evidence of retention, so it resets the streak and schedules a review tomorrow
same as a wrong answer would. This mirrors the confidence-aware mastery reasoning above:
the same signal (confidence + correctness) that tells us how much to trust an answer also
tells us when to ask it again. See due_for_review() for reading the schedule back out.

The same edge also holds attempt_count: a lifetime counter incremented on every
record_attempt() call for that student/question pair, regardless of session or
correctness — unlike streak (which resets on a weak/wrong answer), it never resets. A
question with a high attempt_count but a still-low streak is a signal on its own: the
student keeps coming back to it and still isn't landing a confident-correct answer,
which plain correct/incorrect history doesn't surface as clearly.

record_attempt() also updates a Student-[:MASTERS]->Topic edge on the same leaf topic
:BELONGS_TO targets, holding a Bayesian Knowledge Tracing p_know estimate (see
src/quiz/mastery.py for the algorithm and why it beats a raw accuracy average). Unlike
streak/interval_days (pure Cypher, computed entirely from properties already in scope),
the BKT update needs the *previous* p_know value before it can compute the new one, so
it's read back with a small query before the main write, updated in Python via
mastery.bkt_update(), and passed in as a parameter — Cypher owns graph shape here, Python
owns the numeric algorithm, the same split src/quiz/bank.py's correct_option_index() uses
for grading. A student's first attempt on a question in a given topic has no :MASTERS
edge yet, so current p_know defaults to mastery.BKT_PARAMS["p_init"].

This read-then-write is two separate Cypher statements rather than one, which opens a
narrow window: two concurrent record_attempt() calls for the *same* student+topic could
both read the same p_know and one update would be lost (last-write-wins on the final
SET). This is accepted as a known limitation rather than closed with an explicit
transaction — the intended usage pattern is one attempt in flight per student at a time
(a student answering one question, then the next), so the race is theoretical rather
than a realistic double-submit path.

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
from datetime import datetime

from neo4j import Driver

from src.quiz.mastery import BKT_PARAMS, bkt_update

_CURRENT_MASTERY_QUERY = """
MATCH (s:Student {id: $student_id})
OPTIONAL MATCH (s)-[m:MASTERS]->(t:Topic {path: $topic_path})
RETURN coalesce(m.p_know, $p_init) AS p_know
"""

_RECORD_ATTEMPT_QUERY = """
MATCH (s:Student {id: $student_id})
MATCH (s)-[:ATTEMPTED]->(sess:QuizSession {id: $session_id})
WITH s, sess, CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END AS effective_ts
CREATE (e:InteractionEvent {
    id: $event_id,
    type: "QUIZ_ANSWER",
    question_uid: $question_uid,
    selected_index: $selected_index,
    correct: $correct,
    confidence: $confidence,
    time_taken_seconds: $time_taken_seconds,
    ts: effective_ts
})
CREATE (sess)-[:HAS_ANSWER]->(e)
MERGE (q:Question {uid: $question_uid})
CREATE (e)-[:FOR_QUESTION]->(q)
WITH s, e, q, effective_ts
MERGE (s)-[r:REVIEWING]->(q)
ON CREATE SET r.streak = 0, r.attempt_count = 0
WITH s, e, q, r, effective_ts,
     $correct AND $confidence = "confident" AS strong_pass
SET r.streak = CASE WHEN strong_pass THEN coalesce(r.streak, 0) + 1 ELSE 0 END,
    r.interval_days = CASE WHEN strong_pass THEN toInteger(2 ^ (coalesce(r.streak, 0) + 1)) ELSE 1 END,
    r.attempt_count = coalesce(r.attempt_count, 0) + 1,
    r.last_reviewed_at = effective_ts
WITH s, e, q, r, effective_ts, CASE WHEN r.interval_days > 60 THEN 60 ELSE r.interval_days END AS capped_interval
SET r.interval_days = capped_interval,
    r.next_review_at = effective_ts + duration({days: capped_interval})
WITH s, e, q
CALL (q, s) {
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
    WITH q, s, topics
    UNWIND topics[0..1] AS leaf
    MERGE (q)-[:BELONGS_TO]->(leaf)
    WITH s, leaf
    MERGE (s)-[m:MASTERS]->(leaf)
    SET m.p_know = $new_p_know, m.updated_at = datetime()
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
    ts: datetime | None = None,
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
    level instead of living only as a flat string property on the attempt.

    `ts` backdates the event/REVIEWING timestamps (last_reviewed_at, next_review_at is
    computed from it too) for synthetic history generation — real callers omit it and get
    datetime(). Callers backdating a sequence of attempts on the same question must call
    them in chronological order, since each call's streak/interval builds on the previous
    one's stored value regardless of the ts passed. The same chronological-order
    requirement applies to the MASTERS p_know update (see module docstring)."""
    event_id = str(uuid.uuid4())
    topic_path = " > ".join(topic_tag)
    with driver.session() as session:
        current = session.run(
            _CURRENT_MASTERY_QUERY,
            student_id=student_id,
            topic_path=topic_path,
            p_init=BKT_PARAMS["p_init"],
        ).single()
        current_p_know = current["p_know"] if current else BKT_PARAMS["p_init"]
        new_p_know = bkt_update(current_p_know, correct, BKT_PARAMS)

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
            new_p_know=new_p_know,
            ts=ts.isoformat() if ts else None,
        ).single()
    return record["event_id"] if record else None


_TOPIC_PROGRESS_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(:QuizSession)-[:HAS_ANSWER]->(e:InteractionEvent {type: "QUIZ_ANSWER"})
WHERE e.question_uid IN $question_uids
RETURN e.question_uid AS question_uid, e.correct AS correct, e.confidence AS confidence,
       e.time_taken_seconds AS time_taken_seconds, e.ts AS ts
ORDER BY e.ts ASC
"""


def attempts_for_questions(
    driver: Driver, student_id: str, question_uids: list[str]
) -> list[dict]:
    """Returns every attempt this student made on the given question uids, oldest first."""
    with driver.session() as session:
        records = session.run(
            _TOPIC_PROGRESS_QUERY,
            student_id=student_id,
            question_uids=question_uids,
        )
        return [dict(r) for r in records]


_DUE_FOR_REVIEW_QUERY = """
MATCH (s:Student {id: $student_id})-[r:REVIEWING]->(q:Question)
WHERE r.next_review_at <= datetime()
RETURN q.uid AS question_uid, r.streak AS streak, r.interval_days AS interval_days,
       r.attempt_count AS attempt_count,
       r.last_reviewed_at AS last_reviewed_at, r.next_review_at AS next_review_at
ORDER BY r.next_review_at ASC
LIMIT $limit
"""


def due_for_review(driver: Driver, student_id: str, limit: int = 50) -> list[dict]:
    """Returns this student's questions whose spaced-repetition schedule (see
    record_attempt()'s :REVIEWING update) says they're due now, soonest-due first. A
    question never answered yet has no :REVIEWING edge and so never appears here — this
    is a "what to re-ask" queue, not a "what's left" queue."""
    with driver.session() as session:
        records = session.run(
            _DUE_FOR_REVIEW_QUERY,
            student_id=student_id,
            limit=limit,
        )
        return [dict(r) for r in records]
