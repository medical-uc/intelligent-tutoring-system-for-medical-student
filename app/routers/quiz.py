from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.schemas import (
    CheckAnswerRequest,
    CheckAnswerResponse,
    LogAttemptRequest,
    LogAttemptResponse,
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


def _get_question_or_404(bank: QuestionBank, uid: str) -> Question:
    question = bank.get(uid)
    if question is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such question")
    return question


def _validate_selected_index(question: Question, selected_index: int) -> None:
    if selected_index < 0 or selected_index >= len(question.options):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="selected_index out of range")


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


@router.post("/questions/{uid}/check", response_model=CheckAnswerResponse)
def check_answer(
    uid: str,
    body: CheckAnswerRequest,
    bank: QuestionBank = Depends(_get_bank),
) -> CheckAnswerResponse:
    """Pure grading — no auth, no graph write. Lets the frontend show instant right/wrong
    feedback before the student has picked a confidence level."""
    question = _get_question_or_404(bank, uid)
    _validate_selected_index(question, body.selected_index)

    correct_index = question.correct_option_index()
    return CheckAnswerResponse(correct=body.selected_index == correct_index, correct_index=correct_index)


@router.post("/questions/{uid}/log", response_model=LogAttemptResponse)
def log_attempt(
    uid: str,
    body: LogAttemptRequest,
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> LogAttemptResponse:
    """Single write for the whole attempt — called once both selected_index and confidence
    are known, so exactly one InteractionEvent is created (never a partial one)."""
    question = _get_question_or_404(bank, uid)
    _validate_selected_index(question, body.selected_index)

    correct_index = question.correct_option_index()
    is_correct = body.selected_index == correct_index

    event_id = record_attempt(
        driver,
        student_id=student_id,
        question_uid=uid,
        selected_index=body.selected_index,
        correct=is_correct,
        confidence=body.confidence.value,
    )

    return LogAttemptResponse(event_id=event_id, correct=is_correct)
