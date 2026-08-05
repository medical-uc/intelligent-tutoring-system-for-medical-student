"""Flashcards derived from the question bank — pure recall, no options shown.

A flashcard is a (front, back) pair built from an existing quiz Question: front is the
stem, back is the correct option's text. This reuses src.quiz.bank as the single source
of question content rather than introducing a second dataset — the bank already carries
topic_tag and difficulty, which flashcards need for the same topic-scoped browsing quiz
questions get.

Usage:
    from src.flashcards.cards import Flashcard, flashcards_for_topic
    flashcards_for_topic("ENDOCRINOLOGY > FUNCTIONS OF HORMONE")
"""

from dataclasses import dataclass

from src.quiz.bank import Question, QuestionBank, load_question_bank


@dataclass(frozen=True)
class Flashcard:
    uid: str
    front: str
    back: str
    explanation: str
    topic_tag: list[str]
    difficulty: int


def _to_flashcard(q: Question) -> Flashcard:
    correct_index = q.correct_option_index()
    return Flashcard(
        uid=q.uid,
        front=q.stem,
        back=q.options[correct_index].text,
        explanation=q.explanation,
        topic_tag=q.topic_tag,
        difficulty=q.difficulty,
    )


def flashcards_for_topic(topic_path: str, bank: QuestionBank | None = None) -> list[Flashcard]:
    bank = bank or load_question_bank()
    return [_to_flashcard(q) for q in bank.questions_for_topic(topic_path)]


def get_flashcard(uid: str, bank: QuestionBank | None = None) -> Flashcard | None:
    bank = bank or load_question_bank()
    question = bank.get(uid)
    return _to_flashcard(question) if question else None
