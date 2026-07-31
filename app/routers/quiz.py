from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.schemas import (
    AnswerSubmitRequest,
    AnswerSubmitResponse,
    OptionOut,
    QuestionOut,
    TopicListResponse,
)
from src.quiz.attempts import record_attempt
from src.quiz.bank import Question, QuestionBank, load_question_bank

router = APIRouter(prefix="/quiz", tags=["quiz"])


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


@router.get("/topics", response_model=TopicListResponse)
def list_topics(bank: QuestionBank = Depends(_get_bank)) -> TopicListResponse:
    return TopicListResponse(topics=bank.topics())


@router.get("/topics/{topic_path:path}/questions", response_model=list[QuestionOut])
def get_questions_for_topic(
    topic_path: str,
    bank: QuestionBank = Depends(_get_bank),
) -> list[QuestionOut]:
    questions = bank.questions_for_topic(topic_path)
    if not questions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no questions for this topic")
    return [_to_question_out(q) for q in questions]


@router.post("/questions/{uid}/answer", response_model=AnswerSubmitResponse)
def submit_answer(
    uid: str,
    body: AnswerSubmitRequest,
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> AnswerSubmitResponse:
    question = bank.get(uid)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such question")
    if body.selected_index < 0 or body.selected_index >= len(question.options):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="selected_index out of range")

    correct_index = question.correct_option_index()
    is_correct = body.selected_index == correct_index

    record_attempt(
        driver,
        student_id=student_id,
        question_uid=uid,
        selected_index=body.selected_index,
        correct=is_correct,
    )

    return AnswerSubmitResponse(correct=is_correct, correct_index=correct_index)
