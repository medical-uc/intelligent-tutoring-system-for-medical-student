"""Loads and sanitizes the generated MCQ bank for serving to students.

question_bank.json is nested {doc_id: {topic_path: [question, ...]}} and each question
embeds the answer key inline (options[].correct) plus internal-only fields (critique,
triple, source, distractor_sources). None of that may reach the client before an answer
is submitted — this module flattens the bank into quiz-ready questions and is the single
place that knows both the raw shape and which fields are answer-bearing. `explanation` is
answer-bearing too (it names the correct option's reasoning) but is fine to reveal once
the attempt is graded — see app/routers/quiz.py's /check endpoint.

Usage:
    from src.quiz.bank import load_question_bank
    bank = load_question_bank()          # cached after first call
    bank.topics()                        # -> list[str]
    bank.questions_for_topic("ENDOCRINOLOGY > FUNCTIONS OF HORMONE")
    bank.get(uid)                        # -> Question | None, for answer-checking
"""

import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache

log = logging.getLogger("quiz.bank")

DEFAULT_BANK_PATH = os.environ.get(
    "QUESTION_BANK_PATH",
    "notebooks/mcq_output/question_bank.json",
)


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

    def __len__(self) -> int:
        return len(self._by_uid)


def _is_usable(raw_question: dict) -> bool:
    critique = raw_question.get("critique") or {}
    return critique.get("valid_as_generated", True) is not False


def _parse_question(raw_question: dict) -> Question:
    return Question(
        uid=raw_question["uid"],
        stem=raw_question["stem"],
        options=[
            Option(text=o["text"], correct=o["correct"])
            for o in raw_question["options"]
        ],
        topic_tag=raw_question.get("topic_tag", []),
        difficulty=raw_question.get("difficulty", 1),
        explanation=raw_question.get("explanation", ""),
    )


def _load_from_disk(path: str) -> QuestionBank:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    questions: list[Question] = []
    skipped = 0
    for _doc_id, topics in raw.items():
        for _topic_path, raw_questions in topics.items():
            for rq in raw_questions:
                if not _is_usable(rq):
                    skipped += 1
                    continue
                questions.append(_parse_question(rq))

    log.info(
        "loaded %d questions from %s (%d skipped as invalid)",
        len(questions),
        path,
        skipped,
    )
    return QuestionBank(questions)


@lru_cache(maxsize=1)
def load_question_bank(path: str = DEFAULT_BANK_PATH) -> QuestionBank:
    return _load_from_disk(path)
