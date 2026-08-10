"""Energy — light-gamification currency awarded for finishing study activity.

Stored as a single running-total property on the Student node (s.energy), not a ledger
of award events — the frontend only needs a current balance to display, and every award
site (src.quiz.sessions.end_session, src.flashcards.reviews.record_review) already
writes through its own Cypher in one round trip, so the increment is folded into that
same query rather than issued as a separate write here (see ENERGY_PER_QUIZ_SESSION/
ENERGY_PER_FLASHCARD_REVIEW usages there). This module just holds the award amounts and
a read-only balance lookup.

Flat awards, not scaled to quiz correctness or flashcard rating — this is meant to
reward showing up and finishing, like a Duolingo-style currency, not a second mastery
signal (that's already what :REVIEWING/Flashcard.streak are for).

Usage:
    from src.student_kg.energy import get_energy
    get_energy(driver, student_id)  # -> int
"""

from neo4j import Driver

ENERGY_PER_QUIZ_SESSION = 10  # awarded once, on a *completed* session only — see
# src.quiz.sessions.end_session. Cancelled/abandoned sessions award nothing.
ENERGY_PER_FLASHCARD_REVIEW = 2  # awarded per card rated, in src.flashcards.reviews.record_review

_GET_ENERGY_QUERY = """
MATCH (s:Student {id: $student_id})
RETURN coalesce(s.energy, 0) AS energy
"""


def get_energy(driver: Driver, student_id: str) -> int | None:
    """Current energy balance, or None if student_id doesn't match a Student node."""
    with driver.session() as session:
        record = session.run(_GET_ENERGY_QUERY, student_id=student_id).single()
    return record["energy"] if record else None
