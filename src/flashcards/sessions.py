"""Flashcard sessions — Anki-style fixed-size batches instead of dumping the whole deck.

A FlashcardSession fixes its card_uids list once at start_session() time and hands it back
to the client — the client walks that list card-by-card (reveal + log per uid), rather
than the server tracking a live cursor. This mirrors the QuizSession shape exactly
(src/quiz/sessions.py + src/quiz/attempts.py): FlashcardSession is itself an
InteractionEvent (type: FLASHCARD_SESSION) linked to the student via :ATTEMPTED plus the
shared :NEXT/:LATEST_EVENT timeline chain, and each rating logged via
src.flashcards.reviews.record_review links back to its session via
(sess)-[:HAS_ANSWER]->(FLASHCARD_REVIEW event) — same edge name/shape quiz uses for
QUIZ_ANSWER events. record_review() now requires a session_id for exactly this reason: a
rating with no session to attach to isn't a well-formed FLASHCARD_REVIEW event any more
than a QUIZ_ANSWER without one would be.

Card selection is due-first, new-second: cards this student has reviewed before whose
schedule (src.flashcards.reviews) says they're due now come first (soonest-due first),
then never-reviewed cards from the requested scope top up the batch to `size`. This is the
same due_for_review query flashcards/reviews.py already exposes, so a card never reviewed
yet still gets picked up as "new" rather than silently excluded.

Unlike QuizSession, a FlashcardSession does not gate energy — rating a card via
record_review() already awards ENERGY_PER_FLASHCARD_REVIEW and updates that card's
schedule on every call, session or no session (there's now always one). The session only
groups a batch for the "N cards, done" UI and history row; ending/cancelling it aggregates
how many FLASHCARD_REVIEW events are linked to it via :HAS_ANSWER, read straight off the
graph rather than cross-referencing card_uids against timestamps.

Usage:
    from src.flashcards.sessions import start_session, end_session, history_for_student
    session = start_session(driver, student_id, bank, topic_path="...", size=10)
    # ... reveal_card, then record_review(..., session_id=session["session_id"]) per uid ...
    end_session(driver, student_id, session["session_id"])
    history_for_student(driver, student_id)
"""

import uuid
from datetime import datetime

from neo4j import Driver

from src.flashcards.cards import all_flashcards, flashcards_for_topic
from src.flashcards.reviews import due_for_review
from src.quiz.bank import QuestionBank

DEFAULT_SESSION_SIZE = 10


def _select_card_uids(
    driver: Driver,
    student_id: str,
    bank: QuestionBank,
    topic_path: str | None,
    size: int,
) -> list[str]:
    """Due cards first (soonest-due), then never-reviewed cards from the scope, capped at
    size. Scope is the whole bank when topic_path is None, else just that topic."""
    scoped_cards = (
        flashcards_for_topic(topic_path, bank=bank)
        if topic_path is not None
        else all_flashcards(bank=bank)
    )
    scoped_uids = {c.uid for c in scoped_cards}

    due = due_for_review(driver, student_id=student_id, limit=size)
    due_uids = [r["question_uid"] for r in due if r["question_uid"] in scoped_uids]

    remaining = size - len(due_uids)
    new_uids: list[str] = []
    if remaining > 0:
        seen = set(due_uids)
        for card in scoped_cards:
            if card.uid in seen:
                continue
            new_uids.append(card.uid)
            if len(new_uids) == remaining:
                break

    return due_uids + new_uids


_START_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})
OPTIONAL MATCH (s)-[latest:LATEST_EVENT]->(prev:InteractionEvent)
WITH s, prev, latest, CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END AS effective_ts
CREATE (sess:InteractionEvent:FlashcardSession {
    id: $session_id,
    type: "FLASHCARD_SESSION",
    topic_path: $topic_path,
    card_uids: $card_uids,
    status: "in_progress",
    started_at: effective_ts,
    ended_at: null,
    card_count: size($card_uids),
    reviewed_count: null,
    duration_seconds: null,
    ts: effective_ts
})
CREATE (s)-[:ATTEMPTED]->(sess)
FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END | CREATE (prev)-[:NEXT]->(sess))
DELETE latest
CREATE (s)-[:LATEST_EVENT]->(sess)
RETURN sess.id AS session_id, sess.card_uids AS card_uids
"""


def start_session(
    driver: Driver,
    student_id: str,
    bank: QuestionBank,
    topic_path: str | None = None,
    size: int = DEFAULT_SESSION_SIZE,
    ts: datetime | None = None,
) -> dict | None:
    """Picks up to `size` card uids (due first, then new) from topic_path's cards, or the
    whole bank if topic_path is None, and creates a FlashcardSession pinning that exact
    list. Returns {"session_id", "card_uids"}, or None if the scope has no cards at all.

    `ts` backdates started_at/ts for synthetic history generation — real callers omit it.
    """
    card_uids = _select_card_uids(
        driver, student_id=student_id, bank=bank, topic_path=topic_path, size=size
    )
    if not card_uids:
        return None

    session_id = str(uuid.uuid4())
    with driver.session() as session:
        record = session.run(
            _START_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
            topic_path=topic_path,
            card_uids=card_uids,
            ts=ts.isoformat() if ts else None,
        ).single()
    assert record, f"session start failed — no student with id={student_id}"
    return {"session_id": record["session_id"], "card_uids": record["card_uids"]}


_END_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(sess:FlashcardSession {id: $session_id})
OPTIONAL MATCH (sess)-[:HAS_ANSWER]->(e:InteractionEvent {type: "FLASHCARD_REVIEW"})
WITH s, sess, count(e) AS reviewed_count,
     CASE WHEN $ts IS NULL THEN datetime() ELSE datetime($ts) END AS effective_ts
SET sess.status = "completed",
    sess.ended_at = effective_ts,
    sess.reviewed_count = reviewed_count,
    sess.duration_seconds = duration.inSeconds(sess.started_at, effective_ts).seconds
RETURN sess.id AS session_id, sess.card_count AS card_count, sess.reviewed_count AS reviewed_count,
       sess.duration_seconds AS duration_seconds
"""


def end_session(
    driver: Driver, student_id: str, session_id: str, ts: datetime | None = None
) -> dict | None:
    """Finalizes a session: counts FLASHCARD_REVIEW events linked to it via :HAS_ANSWER,
    marks it completed. Reviews themselves (schedule, energy) are untouched — they already
    happened via record_review() when logged. Returns None if no such in-progress session
    exists for this student.

    `ts` backdates ended_at for synthetic history generation — real callers omit it."""
    with driver.session() as session:
        record = session.run(
            _END_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
            ts=ts.isoformat() if ts else None,
        ).single()
    return dict(record) if record else None


_CANCEL_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(sess:FlashcardSession {id: $session_id, status: "in_progress"})
OPTIONAL MATCH (sess)-[:HAS_ANSWER]->(e:InteractionEvent {type: "FLASHCARD_REVIEW"})
WITH s, sess, count(e) AS reviewed_count
SET sess.status = "cancelled",
    sess.ended_at = datetime(),
    sess.reviewed_count = reviewed_count,
    sess.duration_seconds = duration.inSeconds(sess.started_at, datetime()).seconds
RETURN sess.id AS session_id, sess.card_count AS card_count, sess.reviewed_count AS reviewed_count,
       sess.duration_seconds AS duration_seconds
"""


def cancel_session(driver: Driver, student_id: str, session_id: str) -> dict | None:
    """Ends a session early, same :HAS_ANSWER aggregation as end_session but status
    "cancelled". Matches only status "in_progress", so cancelling an already-finished
    session is a no-op (returns None) rather than overwriting its real
    ended_at/duration_seconds. Already-logged reviews and their schedule updates are not
    undone."""
    with driver.session() as session:
        record = session.run(
            _CANCEL_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
        ).single()
    return dict(record) if record else None


_HISTORY_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(sess:FlashcardSession)
WHERE sess.status IN ["completed", "cancelled"]
RETURN sess.id AS session_id, sess.topic_path AS topic_path, sess.status AS status,
       sess.card_count AS card_count, sess.reviewed_count AS reviewed_count,
       sess.duration_seconds AS duration_seconds, sess.started_at AS started_at,
       sess.ended_at AS ended_at
ORDER BY sess.started_at DESC
LIMIT $limit
"""


def history_for_student(
    driver: Driver, student_id: str, limit: int = 100
) -> list[dict]:
    """Completed/cancelled flashcard sessions for a student, most recent first."""
    with driver.session() as session:
        records = session.run(
            _HISTORY_QUERY,
            student_id=student_id,
            limit=limit,
        )
        return [dict(r) for r in records]


_GET_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})-[:ATTEMPTED]->(sess:FlashcardSession {id: $session_id, status: "in_progress"})
RETURN sess.id AS session_id, sess.card_uids AS card_uids, sess.topic_path AS topic_path
"""


def get_active_session(driver: Driver, student_id: str, session_id: str) -> dict | None:
    """Fetches an in-progress session's pinned card_uids — for a client that reloads
    mid-session and needs to recover its batch. Returns None if not found/not in-progress."""
    with driver.session() as session:
        record = session.run(
            _GET_SESSION_QUERY,
            student_id=student_id,
            session_id=session_id,
        ).single()
    return dict(record) if record else None
