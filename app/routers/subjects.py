from fastapi import APIRouter, Depends, HTTPException, status

from app.openapi import error_responses
from app.schemas import (
    OptionOut,
    QuestionOut,
    SubjectListResponse,
    SubjectOut,
    SubjectTopicOut,
    TopicListResponse,
)
from src.quiz.bank import Question, QuestionBank, load_question_bank

router = APIRouter(prefix="/subjects", tags=["subjects"])


def _get_bank() -> QuestionBank:
    return load_question_bank()


def _to_question_out(q: Question) -> QuestionOut:
    return QuestionOut(
        uid=q.uid,
        stem=q.stem,
        options=[OptionOut(index=i, text=o.text) for i, o in enumerate(q.options)],
        topic_tag=q.topic_tag,
        difficulty=q.difficulty,
    )


@router.get("", response_model=SubjectListResponse, responses=error_responses(401))
def list_subjects(bank: QuestionBank = Depends(_get_bank)) -> SubjectListResponse:
    """Subject -> topic catalog for the Subjects browse page. flashcard_count mirrors
    question_count since every flashcard is 1:1-derived from a bank question (see
    src/flashcards/cards.py) -- there is no separate flashcard content to count."""
    return SubjectListResponse(
        subjects=[
            SubjectOut(
                name=subject["name"],
                question_count=sum(t["question_count"] for t in subject["topics"]),
                flashcard_count=sum(t["question_count"] for t in subject["topics"]),
                topics=[
                    SubjectTopicOut(
                        path=t["path"],
                        name=t["name"],
                        question_count=t["question_count"],
                        flashcard_count=t["question_count"],
                    )
                    for t in subject["topics"]
                ],
            )
            for subject in bank.subjects()
        ]
    )


@router.get(
    "/topics", response_model=TopicListResponse, responses=error_responses(401)
)
def list_topics(bank: QuestionBank = Depends(_get_bank)) -> TopicListResponse:
    return TopicListResponse(topics=bank.topics())


@router.get(
    "/{topic_path:path}/questions",
    response_model=list[QuestionOut],
    responses=error_responses(401, 404),
)
def get_questions_for_topic(
    topic_path: str,
    bank: QuestionBank = Depends(_get_bank),
) -> list[QuestionOut]:
    questions = bank.questions_for_topic(topic_path)
    if not questions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no questions for this topic"
        )
    return [_to_question_out(q) for q in questions]
