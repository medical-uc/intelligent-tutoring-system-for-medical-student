"""Loads and sanitizes the MCQ bank for serving to students.

Questions are served from the `mcq_questions` table in postgres (populated by
scripts/populate_mcq_postgres.py from notebooks/master_mcq_with_topics.json). Each row
embeds the answer key inline (options[].correct) — that may not reach the client before
an answer is submitted, so this module is the single place that knows both the raw shape
and which fields are answer-bearing. `explanation` is answer-bearing too (it names the
correct option's reasoning) but is fine to reveal once the attempt is graded — see
app/routers/quiz.py's /check endpoint.

Usage:
    from src.quiz.bank import load_question_bank
    bank = load_question_bank()          # cached after first call
    bank.topics()                        # -> list[str]
    bank.questions_for_topic("ENDOCRINOLOGY > FUNCTIONS OF HORMONE")
    bank.get(uid)                        # -> Question | None, for answer-checking
"""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger("quiz.bank")

SELECT_QUESTIONS = """
SELECT uid, subject, topic, stem, options, difficulty, explanation
FROM mcq_questions
"""


def _dsn_from_env() -> str:
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    db = os.environ["POSTGRES_DB"]
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


@dataclass(frozen=True)
class Option:
    text: str
    correct: bool


@dataclass(frozen=True)
class Question:
    uid: str
    stem: str
    options: list[Option]
    topic_tag: list[str]
    difficulty: int
    explanation: str = ""

    @property
    def topic_path(self) -> str:
        return " > ".join(self.topic_tag)

    def correct_option_index(self) -> int:
        for i, opt in enumerate(self.options):
            if opt.correct:
                return i
        raise ValueError(f"question {self.uid} has no correct option")


class QuestionBank:
    def __init__(self, questions: list[Question]) -> None:
        self._by_uid = {q.uid: q for q in questions}
        self._by_topic: dict[str, list[Question]] = {}
        for q in questions:
            self._by_topic.setdefault(q.topic_path, []).append(q)

    def topics(self) -> list[str]:
        return sorted(self._by_topic.keys())

    def questions_for_topic(self, topic_path: str) -> list[Question]:
        return list(self._by_topic.get(topic_path, []))

    def get(self, uid: str) -> Question | None:
        return self._by_uid.get(uid)

    def all(self) -> list[Question]:
        return list(self._by_uid.values())

    def __len__(self) -> int:
        return len(self._by_uid)

    def subjects(self) -> list[dict]:
        """Groups topics() by their topic_tag[0] (subject) for the Subjects browse page.

        Each entry: {"name": subject, "topics": [{"path", "name", "question_count"}, ...]}
        sorted by subject name, topics sorted by name. A topic whose tag is just
        [subject] (no leaf segment — some source rows repeat the subject as its own
        topic, e.g. "Anatomy of Neck > Anatomy of Neck") keeps its full topic_path as
        `name` same as any other topic, so it isn't silently merged into the subject
        header."""
        by_subject: dict[str, list[str]] = {}
        for topic_path in self.topics():
            subject = topic_path.split(" > ")[0]
            by_subject.setdefault(subject, []).append(topic_path)

        return [
            {
                "name": subject,
                "topics": [
                    {
                        "path": topic_path,
                        "name": topic_path.split(" > ")[-1],
                        "question_count": len(self._by_topic[topic_path]),
                    }
                    for topic_path in sorted(topic_paths)
                ],
            }
            for subject, topic_paths in sorted(by_subject.items())
        ]


def _parse_row(row: dict) -> Question:
    topic_tag = [t for t in (row["subject"], row["topic"]) if t]
    return Question(
        uid=row["uid"],
        stem=row["stem"],
        options=[Option(text=o["text"], correct=o["correct"]) for o in row["options"]],
        topic_tag=topic_tag,
        difficulty=row["difficulty"],
        explanation=row["explanation"],
    )


def _load_from_postgres(dsn: str) -> QuestionBank:
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(SELECT_QUESTIONS)
            rows = cur.fetchall()

    questions = [_parse_row(row) for row in rows]
    log.info("loaded %d questions from postgres", len(questions))
    return QuestionBank(questions)


@lru_cache(maxsize=1)
def load_question_bank(dsn: str | None = None) -> QuestionBank:
    return _load_from_postgres(dsn or _dsn_from_env())
