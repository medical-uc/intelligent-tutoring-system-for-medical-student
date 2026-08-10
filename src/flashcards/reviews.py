"""Records flashcard recall reviews and drives their spaced-repetition schedule.

Flashcards test pure recall (no options, no correct/incorrect grading) — the student
self-rates how well they recalled the back before it was revealed, using the classic
Again/Hard/Good/Easy scale. This is intentionally a different signal from quiz mastery
(src.quiz.attempts.record_attempt's :REVIEWING edge, which tracks correctness+confidence
on graded MCQ answers), so reviews are tracked on their own Flashcard node rather than
reusing :REVIEWING — a card drilled to "Easy" and a question answered "confident and
correct" are different kinds of evidence and neither should silently update the other's
schedule.

A Flashcard is its own node, not an edge-with-properties: (:Student)-[:HAS_FLASHCARD]->
(:Flashcard {uid, question_uid, streak, interval_days, last_reviewed_at, next_review_at})
-[:FOR_QUESTION]->(:Question). It is scoped to one student+question pair (uid is a
deterministic hash of the two, so re-reviewing the same card MERGEs onto the same node
rather than creating duplicates) — the content itself (front/back/explanation) still
lives entirely on Question via src.flashcards.cards, so the Flashcard node here is purely
this student's recall-schedule state, not a second copy of the card's text.

Interval growth per rating (days, capped at 60 like quiz's REVIEWING schedule):
  again -> streak resets to 0, review again tomorrow (interval 1)
  hard  -> streak unchanged, interval grows by 1.2x (min 1)
  good  -> streak+1, interval doubles (same curve as quiz's "strong pass")
  easy  -> streak+1, interval grows by 2.5x (rewards clearly-solid recall)

Every review belongs to a FlashcardSession (see src.flashcards.sessions) — the frontend
starts a session before the first card in a batch and passes its id to every
record_review() call, which links the FLASHCARD_REVIEW event to that session via
:HAS_ANSWER, same shape as quiz's QuizSession/:HAS_ANSWER. This lets a session's progress
be read straight off its own linked events instead of cross-referencing the card_uids list
against timestamps.

Usage:
    from src.flashcards.reviews import record_review, due_for_review
    record_review(driver, student_id, session_id="...", question_uid="...", rating="good")
    due_for_review(driver, student_id)
"""

import hashlib
import uuid
from datetime import datetime

from neo4j import Driver

from src.student_kg.energy import ENERGY_PER_FLASHCARD_REVIEW

_RATING_MULTIPLIERS = {
    "again": 0.0,  # handled as a hard reset, not a multiply
    "hard": 1.2,
    "good": 2.0,
    "easy": 2.5,
}

_MAX_INTERVAL_DAYS = 60


def _flashcard_uid(student_id: str, question_uid: str) -> str:
    """Deterministic per student+question id, so repeated reviews of the same card MERGE
    onto the same Flashcard node instead of minting a new one each time."""
    return hashlib.sha1(f"{student_id}::{question_uid}".encode()).hexdigest()


_RECORD_REVIEW_QUERY = """
MATCH (s:Student {id: $student_id})
MATCH (s)-[:ATTEMPTED]->(sess:FlashcardSession {id: $session_id})
MERGE (q:Question {uid: $question_uid})
MERGE (s)-[:HAS_FLASHCARD]->(f:Flashcard {uid: $flashcard_uid})
ON CREATE SET f.question_uid = $question_uid, f.streak = 0, f.interval_days = 1
MERGE (f)-[:FOR_QUESTION]->(q)
WITH s, sess, f, CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END AS effective_ts
CREATE (e:InteractionEvent {
    id: $event_id,
    type: "FLASHCARD_REVIEW",
    question_uid: $question_uid,
    rating: $rating,
    ts: effective_ts
})
CREATE (e)-[:FOR_FLASHCARD]->(f)
CREATE (sess)-[:HAS_ANSWER]->(e)
WITH s, e, f, effective_ts, f.streak AS prev_streak, coalesce(f.interval_days, 1) AS prev_interval
SET f.streak = CASE WHEN $rating = "again" THEN 0 ELSE prev_streak + 1 END,
    f.interval_days = CASE
        WHEN $rating = "again" THEN 1
        ELSE toInteger(ceil(prev_interval * $multiplier))
    END,
    f.last_reviewed_at = effective_ts
WITH s, e, f, effective_ts, CASE WHEN f.interval_days > $max_interval THEN $max_interval
                WHEN f.interval_days < 1 THEN 1
                ELSE f.interval_days END AS capped_interval
SET f.interval_days = capped_interval,
    f.next_review_at = effective_ts + duration({days: capped_interval}),
    s.energy = coalesce(s.energy, 0) + $energy_award
RETURN e.id AS event_id, f.streak AS streak, f.interval_days AS interval_days,
       f.next_review_at AS next_review_at, $energy_award AS energy_awarded,
       s.energy AS energy_balance
"""


def record_review(
    driver: Driver,
    student_id: str,
    session_id: str,
    question_uid: str,
    rating: str,
    ts: datetime | None = None,
) -> dict | None:
    """Persists one flashcard self-rating, links it to its FlashcardSession via
    :HAS_ANSWER (same shape as quiz's record_attempt/QuizSession), updates that card's
    schedule, and awards ENERGY_PER_FLASHCARD_REVIEW energy (see src.student_kg.energy) —
    flat, every rating included ("again" still counts as showing up to review).

    Returns the new event id, streak, interval_days, next_review_at, energy_awarded,
    energy_balance — or None if session_id doesn't match a session belonging to this
    student (client-reachable: stale/foreign id), same contract as quiz's record_attempt.

    `ts` backdates the event/schedule timestamps for synthetic history generation (see
    scripts/generate_dummy_interactions.py) — real callers omit it and get datetime().
    Backdating a sequence of reviews for the same card must be called in chronological
    order, since streak/interval build on the previously stored value."""
    if rating not in _RATING_MULTIPLIERS:
        raise ValueError(f"unknown rating: {rating!r}")

    event_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _RECORD_REVIEW_QUERY,
            student_id=student_id,
            session_id=session_id,
            question_uid=question_uid,
            flashcard_uid=_flashcard_uid(student_id, question_uid),
            event_id=event_id,
            rating=rating,
            multiplier=_RATING_MULTIPLIERS[rating],
            max_interval=_MAX_INTERVAL_DAYS,
            ts=ts.isoformat() if ts else None,
            energy_award=ENERGY_PER_FLASHCARD_REVIEW,
        ).single()
    return dict(record) if record else None


_DUE_FOR_REVIEW_QUERY = """
MATCH (s:Student {id: $student_id})-[:HAS_FLASHCARD]->(f:Flashcard)
WHERE f.next_review_at <= datetime()
RETURN f.question_uid AS question_uid, f.streak AS streak, f.interval_days AS interval_days,
       f.last_reviewed_at AS last_reviewed_at, f.next_review_at AS next_review_at
ORDER BY f.next_review_at ASC
LIMIT $limit
"""


def due_for_review(driver: Driver, student_id: str, limit: int = 50) -> list[dict]:
    """Cards due for re-drilling now, soonest-due first. A card never reviewed yet has no
    Flashcard node and so never appears here — pair with flashcards_for_topic() for
    "drill this topic fresh" flows."""
    with driver.session() as session:
        records = session.run(
            _DUE_FOR_REVIEW_QUERY,
            student_id=student_id,
            limit=limit,
        )
        return [dict(r) for r in records]


_COUNT_DUE_FOR_REVIEW_QUERY = """
MATCH (s:Student {id: $student_id})-[:HAS_FLASHCARD]->(f:Flashcard)
WHERE f.next_review_at <= datetime()
RETURN count(f) AS due_count
"""


def count_due_for_review(driver: Driver, student_id: str) -> int:
    """Cheap count of due cards, for dashboard badges that don't need the full queue."""
    with driver.session() as session:
        record = session.run(
            _COUNT_DUE_FOR_REVIEW_QUERY, student_id=student_id
        ).single()
        return record["due_count"] if record else 0


_REVIEW_HISTORY_QUERY = """
MATCH (s:Student {id: $student_id})-[:HAS_FLASHCARD]->(f:Flashcard {question_uid: $question_uid})
MATCH (e:InteractionEvent {type: "FLASHCARD_REVIEW"})-[:FOR_FLASHCARD]->(f)
RETURN e.id AS event_id, e.rating AS rating, e.ts AS ts
ORDER BY e.ts ASC
"""


def review_history(driver: Driver, student_id: str, question_uid: str) -> list[dict]:
    """Every rating this student has ever given this card, oldest first — the raw
    Again/Hard/Good/Easy trail behind the current streak/interval on the Flashcard node.
    """
    with driver.session() as session:
        records = session.run(
            _REVIEW_HISTORY_QUERY,
            student_id=student_id,
            question_uid=question_uid,
        )
        return [dict(r) for r in records]


_HISTORY_QUERY = """
MATCH (s:Student {id: $student_id})-[:HAS_FLASHCARD]->(f:Flashcard)<-[:FOR_FLASHCARD]-(e:InteractionEvent {type: "FLASHCARD_REVIEW"})
RETURN e.id AS event_id, e.question_uid AS question_uid, e.rating AS rating, e.ts AS ts
ORDER BY e.ts DESC
LIMIT $limit
"""


def history_for_student(
    driver: Driver, student_id: str, limit: int = 100
) -> list[dict]:
    """Every flashcard review this student has ever logged, most recent first — a flat
    feed (one row per rating), unlike quiz history's session-grouped rows, since
    flashcard reviews have no session wrapper. The caller (app/routers/flashcards.py)
    joins question_uid back to the bank for topic/stem display."""
    with driver.session() as session:
        records = session.run(
            _HISTORY_QUERY,
            student_id=student_id,
            limit=limit,
        )
        return [dict(r) for r in records]
