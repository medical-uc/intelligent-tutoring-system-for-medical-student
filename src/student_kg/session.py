"""Server-side session tokens for the student KG API.

Tokens are opaque and random (secrets.token_urlsafe) — never a stateless JWT — because the
backend needs to actually revoke and expire sessions on its own terms. Only a SHA-256 hash of
the token is ever persisted; the raw token is returned to the caller exactly once, at issuance,
so a Neo4j dump/leak can't be replayed as a live session.

Usage:
    from src.student_kg.session import create_session, validate_session, revoke_session
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from neo4j import Driver

log = logging.getLogger("session")

DEFAULT_TTL_HOURS = 24 * 7  # one week


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass
class Session:
    token: str
    student_id: str
    expires_at: datetime


_CREATE_SESSION_QUERY = """
MATCH (s:Student {id: $student_id})
CREATE (sess:Session {
    token_hash: $token_hash,
    student_id: $student_id,
    issued_at: datetime($issued_at),
    expires_at: datetime($expires_at),
    revoked_at: null
})
CREATE (s)-[:HAS_SESSION]->(sess)
RETURN sess.token_hash AS token_hash
"""


def create_session(
    driver: Driver, student_id: str, ttl_hours: int = DEFAULT_TTL_HOURS
) -> Session:
    """Issues a new session for an already-existing student. Raises if student_id doesn't
    match a Student node — callers should enroll/look up the student first."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(hours=ttl_hours)

    with driver.session() as session:
        record = session.run(
            _CREATE_SESSION_QUERY,
            student_id=student_id,
            token_hash=token_hash,
            issued_at=issued_at.isoformat(),
            expires_at=expires_at.isoformat(),
        ).single()
    assert record, f"session creation failed — no student with id={student_id}"

    log.info(
        "issued session for student_id=%s expires_at=%s",
        student_id,
        expires_at.isoformat(),
    )
    return Session(token=token, student_id=student_id, expires_at=expires_at)


_VALIDATE_SESSION_QUERY = """
MATCH (sess:Session {token_hash: $token_hash})
WHERE sess.revoked_at IS NULL AND sess.expires_at > datetime()
RETURN sess.student_id AS student_id
"""


def validate_session(driver: Driver, token: str) -> str | None:
    """Returns the student_id for a live (unexpired, unrevoked) token, or None."""
    token_hash = _hash_token(token)
    with driver.session() as session:
        record = session.run(_VALIDATE_SESSION_QUERY, token_hash=token_hash).single()
    return record["student_id"] if record else None


_REVOKE_SESSION_QUERY = """
MATCH (sess:Session {token_hash: $token_hash})
WHERE sess.revoked_at IS NULL
SET sess.revoked_at = datetime()
RETURN sess.token_hash AS token_hash
"""


def revoke_session(driver: Driver, token: str) -> bool:
    """Revokes a single session (logout). Returns False if the token didn't match a live
    session (already revoked, expired, or never existed)."""
    token_hash = _hash_token(token)
    with driver.session() as session:
        record = session.run(_REVOKE_SESSION_QUERY, token_hash=token_hash).single()
    return record is not None


_REVOKE_ALL_FOR_STUDENT_QUERY = """
MATCH (sess:Session {student_id: $student_id})
WHERE sess.revoked_at IS NULL
SET sess.revoked_at = datetime()
RETURN count(sess) AS revoked_count
"""


def revoke_all_sessions_for_student(driver: Driver, student_id: str) -> int:
    """Revokes every live session for a student — e.g. on password reset or 'log out
    everywhere'. Returns how many sessions were revoked."""
    with driver.session() as session:
        record = session.run(
            _REVOKE_ALL_FOR_STUDENT_QUERY, student_id=student_id
        ).single()
    assert record is not None
    return record["revoked_count"]


_FIND_STUDENT_BY_NUMBER_QUERY = """
MATCH (s:Student {student_number: $student_number})
RETURN s.id AS student_id
"""


def find_student_id_by_number(driver: Driver, student_number: str) -> str | None:
    with driver.session() as session:
        record = session.run(
            _FIND_STUDENT_BY_NUMBER_QUERY, student_number=student_number
        ).single()
    return record["student_id"] if record else None
