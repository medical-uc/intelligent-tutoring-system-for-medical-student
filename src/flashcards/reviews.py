"""Records flashcard recall reviews and drives their spaced-repetition schedule.

Flashcards test pure recall (no options, no correct/incorrect grading) — the student
self-rates how well they recalled the back before it was revealed, using the classic
Again/Hard/Good/Easy scale. This is intentionally a different signal from quiz mastery
(src.quiz.attempts.record_attempt's :REVIEWING edge, which tracks correctness+confidence
on graded MCQ answers), so reviews live on their own :CARD_REVIEWED edge from Student to
Question rather than reusing :REVIEWING — a card drilled to "Easy" and a question answered
"confident and correct" are different kinds of evidence and neither should silently update
the other's schedule.

Interval growth per rating (days, capped at 60 like quiz's REVIEWING schedule):
  again -> streak resets to 0, review again tomorrow (interval 1)
  hard  -> streak unchanged, interval grows by 1.2x (min 1)
  good  -> streak+1, interval doubles (same curve as quiz's "strong pass")
  easy  -> streak+1, interval grows by 2.5x (rewards clearly-solid recall)

Usage:
    from src.flashcards.reviews import record_review, due_for_review
    record_review(driver, student_id, question_uid="...", rating="good")
    due_for_review(driver, student_id)
"""

import uuid

from neo4j import Driver

_RATING_MULTIPLIERS = {
    "again": 0.0,   # handled as a hard reset, not a multiply
    "hard": 1.2,
    "good": 2.0,
    "easy": 2.5,
}

_MAX_INTERVAL_DAYS = 60

_RECORD_REVIEW_QUERY = """
MATCH (s:Student {id: $student_id})
MERGE (q:Question {uid: $question_uid})
CREATE (e:InteractionEvent {
    id: $event_id,
    type: "FLASHCARD_REVIEW",
    question_uid: $question_uid,
    rating: $rating,
    ts: datetime()
})
CREATE (e)-[:FOR_QUESTION]->(q)
MERGE (s)-[r:CARD_REVIEWED]->(q)
ON CREATE SET r.streak = 0, r.interval_days = 1
WITH s, e, q, r, r.streak AS prev_streak, coalesce(r.interval_days, 1) AS prev_interval
SET r.streak = CASE WHEN $rating = "again" THEN 0 ELSE prev_streak + 1 END,
    r.interval_days = CASE
        WHEN $rating = "again" THEN 1
        ELSE toInteger(ceil(prev_interval * $multiplier))
    END,
    r.last_reviewed_at = e.ts
WITH e, r, CASE WHEN r.interval_days > $max_interval THEN $max_interval
                WHEN r.interval_days < 1 THEN 1
                ELSE r.interval_days END AS capped_interval
SET r.interval_days = capped_interval,
    r.next_review_at = datetime() + duration({days: capped_interval})
RETURN e.id AS event_id, r.streak AS streak, r.interval_days AS interval_days,
       r.next_review_at AS next_review_at
"""


def record_review(driver: Driver, student_id: str, question_uid: str, rating: str) -> dict | None:
    """Persists one flashcard self-rating and updates the :CARD_REVIEWED schedule.

    Returns the new event id, streak, interval_days, next_review_at — or None if
    student_id doesn't match a Student node (client-reachable: stale/foreign id)."""
    if rating not in _RATING_MULTIPLIERS:
        raise ValueError(f"unknown rating: {rating!r}")

    event_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _RECORD_REVIEW_QUERY,
            student_id=student_id,
            question_uid=question_uid,
            event_id=event_id,
            rating=rating,
            multiplier=_RATING_MULTIPLIERS[rating],
            max_interval=_MAX_INTERVAL_DAYS,
        ).single()
    return dict(record) if record else None


_DUE_FOR_REVIEW_QUERY = """
MATCH (s:Student {id: $student_id})-[r:CARD_REVIEWED]->(q:Question)
WHERE r.next_review_at <= datetime()
RETURN q.uid AS question_uid, r.streak AS streak, r.interval_days AS interval_days,
       r.last_reviewed_at AS last_reviewed_at, r.next_review_at AS next_review_at
ORDER BY r.next_review_at ASC
LIMIT $limit
"""


def due_for_review(driver: Driver, student_id: str, limit: int = 50) -> list[dict]:
    """Cards due for re-drilling now, soonest-due first. A card never reviewed yet has no
    :CARD_REVIEWED edge and so never appears here — pair with flashcards_for_topic() for
    "drill this topic fresh" flows."""
    with driver.session() as session:
        records = session.run(
            _DUE_FOR_REVIEW_QUERY,
            student_id=student_id,
            limit=limit,
        )
        return [dict(r) for r in records]
