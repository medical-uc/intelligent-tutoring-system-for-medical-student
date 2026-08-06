"""Day streak — consecutive UTC calendar days a student did *any* study activity.

Distinct from src.quiz.attempts's per-question REVIEWING.streak (spaced-repetition
"strong pass" streak on one question) and src.flashcards.reviews's per-card Flashcard.streak
(recall-rating streak on one card) — this is account-level, Duolingo-style: did the student
touch a quiz session or a flashcard review *at all* on a given day.

Computed on read, not stored, so there's no write-path bookkeeping to keep in sync across
two different event sources (QuizSession.started_at, FLASHCARD_REVIEW.ts) — a student's
full activity-day history is cheap to pull (one date per session/review, not per question)
and the consecutive-day walk is done in Python for clarity over a harder-to-read Cypher
date-arithmetic query.

A day counts if a quiz session was *started* that day (matches what the student
experienced, not when a session happened to be finalized) or a flashcard review was
logged that day.

Streak counts backward from today (UTC): today missing activity does not break the
streak (the day isn't over yet), but any earlier gap does. A student who has never done
anything gets a streak of 0.

Usage:
    from src.student_kg.streak import current_streak
    current_streak(driver, student_id)  # -> int
"""

from datetime import UTC, datetime, timedelta

from neo4j import Driver

_ACTIVITY_DAYS_QUERY = """
MATCH (s:Student {id: $student_id})
OPTIONAL MATCH (s)-[:ATTEMPTED]->(sess:QuizSession)
OPTIONAL MATCH (s)-[:HAS_FLASHCARD]->(:Flashcard)<-[:FOR_FLASHCARD]-(rev:InteractionEvent {type: "FLASHCARD_REVIEW"})
WITH collect(DISTINCT date(sess.started_at)) + collect(DISTINCT date(rev.ts)) AS days
UNWIND days AS day
WITH DISTINCT day
WHERE day IS NOT NULL
RETURN day
ORDER BY day DESC
"""


def _active_days(driver: Driver, student_id: str) -> list[datetime]:
    with driver.session() as session:
        records = session.run(_ACTIVITY_DAYS_QUERY, student_id=student_id)
        return [r["day"].to_native() for r in records]


def current_streak(driver: Driver, student_id: str) -> int:
    """Consecutive UTC days of quiz/flashcard activity, walking back from today.

    Today missing an entry doesn't break the streak (day isn't over); the most recent
    gap of a full day or more does. Returns 0 if the student has never studied, or if
    their most recent activity was before yesterday."""
    days = _active_days(driver, student_id)
    if not days:
        return 0

    today = datetime.now(UTC).date()
    most_recent = days[0]
    if most_recent < today - timedelta(days=1):
        return 0

    streak = 0
    expected = most_recent
    for day in days:
        if day == expected:
            streak += 1
            expected = expected - timedelta(days=1)
        elif day < expected:
            break
    return streak


def week_activity(driver: Driver, student_id: str) -> list[bool]:
    """Which days of the current UTC calendar week (Monday..Sunday) have activity, as a
    7-element list indexed Monday-first — matches the M/T/W/T/F/S/S week-view row on the
    dashboard's streak card. Days later in the week than today are simply False (not yet
    happened), same as a day with no activity."""
    days = set(_active_days(driver, student_id))
    today = datetime.now(UTC).date()
    monday = today - timedelta(days=today.weekday())
    return [(monday + timedelta(days=i)) in days for i in range(7)]
