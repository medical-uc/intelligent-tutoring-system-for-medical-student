"""Shared Neo4j driver + schema constraints for the student knowledge graph.

Usage (needs the neo4j stack up: `make neo4j-up`):
    from src.student_kg.driver import make_driver, ensure_constraints
"""

import os

from neo4j import Driver, GraphDatabase


def make_driver() -> Driver:
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    assert password, "NEO4J_PASSWORD not set"
    return GraphDatabase.driver(uri, auth=(user, password))


def ensure_constraints(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT student_id_unique IF NOT EXISTS "
            "FOR (s:Student) REQUIRE s.id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT student_number_unique IF NOT EXISTS "
            "FOR (s:Student) REQUIRE s.student_number IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT event_id_unique IF NOT EXISTS "
            "FOR (e:InteractionEvent) REQUIRE e.id IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT session_token_hash_unique IF NOT EXISTS "
            "FOR (sess:Session) REQUIRE sess.token_hash IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT question_uid_unique IF NOT EXISTS "
            "FOR (q:Question) REQUIRE q.uid IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT topic_path_unique IF NOT EXISTS "
            "FOR (t:Topic) REQUIRE t.path IS UNIQUE"
        )
        session.run(
            "CREATE CONSTRAINT flashcard_uid_unique IF NOT EXISTS "
            "FOR (f:Flashcard) REQUIRE f.uid IS UNIQUE"
        )
