from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.schemas import (
    CancelSessionResponse,
    CheckAnswerRequest,
    CheckAnswerResponse,
    DueReviewItem,
    DueReviewResponse,
    EndSessionResponse,
    HistoryItem,
    HistoryResponse,
    LogAttemptRequest,
    LogAttemptResponse,
    OptionOut,
    QuestionOut,
    StartSessionResponse,
    SubjectListResponse,
    SubjectOut,
    SubjectTopicOut,
    TopicListResponse,
)
from src.quiz.attempts import due_for_review, record_attempt
from src.quiz.bank import Question, QuestionBank, load_question_bank
from src.quiz.sessions import (
    cancel_session,
    end_session,
    history_for_student,
    start_session,
)

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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such question"
        )
    return question


def _validate_selected_index(question: Question, selected_index: int) -> None:
    if selected_index < 0 or selected_index >= len(question.options):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="selected_index out of range",
        )


@router.get("/topics", response_model=TopicListResponse)
def list_topics(bank: QuestionBank = Depends(_get_bank)) -> TopicListResponse:
    return TopicListResponse(topics=bank.topics())


@router.get("/subjects", response_model=SubjectListResponse)
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


@router.get("/topics/{topic_path:path}/questions", response_model=list[QuestionOut])
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


@router.post("/topics/{topic_path:path}/sessions", response_model=StartSessionResponse)
def start_quiz_session(
    topic_path: str,
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> StartSessionResponse:
    """Call once, before the first question of a topic run. The returned session_id must be
    passed to every /log call in this run, then to /sessions/{session_id}/end when done.
    """
    if not bank.questions_for_topic(topic_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no questions for this topic"
        )
    session_id = start_session(driver, student_id=student_id, topic_path=topic_path)
    return StartSessionResponse(session_id=session_id)


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
def end_quiz_session(
    session_id: str,
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> EndSessionResponse:
    """Call once, after the last question of a topic run. Aggregates every answer linked to
    this session into question_count/correct_count/duration_seconds."""
    summary = end_session(driver, student_id=student_id, session_id=session_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such session for this student",
        )
    return EndSessionResponse(**summary)


@router.post("/sessions/{session_id}/cancel", response_model=CancelSessionResponse)
def cancel_quiz_session(
    session_id: str,
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> CancelSessionResponse:
    """Call instead of /end when the student abandons a run partway through (navigates away,
    closes the app) — the frontend should fire this on unmount/beforeunload rather than
    leaving the session stuck "in_progress" forever. Aggregates whatever answers were logged
    before the cancel into question_count/correct_count/duration_seconds, same as /end, but
    marks the session "cancelled" so it reads as partial rather than finished in /history.
    Already-logged answers and their :REVIEWING mastery updates are not undone."""
    summary = cancel_session(driver, student_id=student_id, session_id=session_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such in-progress session for this student",
        )
    return CancelSessionResponse(**summary)


@router.get("/history", response_model=HistoryResponse)
def get_history(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> HistoryResponse:
    """Completed quiz sessions for the current student, most recent first. Day-bucketing
    (Today/Yesterday/This Week) is presentation logic left to the frontend, computed from
    each item's started_at against the client's local timezone."""
    sessions = history_for_student(driver, student_id=student_id)
    items = [
        HistoryItem(
            session_id=s["session_id"],
            topic_path=s["topic_path"],
            status=s["status"],
            question_count=s["question_count"],
            correct_count=s["correct_count"],
            score_percent=(
                round(100 * s["correct_count"] / s["question_count"])
                if s["question_count"]
                else 0
            ),
            duration_seconds=s["duration_seconds"],
            started_at=s["started_at"].to_native(),
            ended_at=s["ended_at"].to_native(),
        )
        for s in sessions
    ]
    return HistoryResponse(items=items)


@router.get("/review/due", response_model=DueReviewResponse)
def get_due_for_review(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> DueReviewResponse:
    """Questions this student has answered before whose spaced-repetition schedule says
    they're due again now, soonest-due first. A question the student has never answered
    has no schedule yet and never appears here — pair with /topics/{path}/questions for
    "answer this for the first time" flows."""
    items = due_for_review(driver, student_id=student_id)
    return DueReviewResponse(
        items=[
            DueReviewItem(
                question_uid=r["question_uid"],
                streak=r["streak"],
                interval_days=r["interval_days"],
                last_reviewed_at=r["last_reviewed_at"].to_native(),
                next_review_at=r["next_review_at"].to_native(),
            )
            for r in items
        ]
    )


@router.post("/questions/{uid}/check", response_model=CheckAnswerResponse)
def check_answer(
    uid: str,
    body: CheckAnswerRequest,
    bank: QuestionBank = Depends(_get_bank),
) -> CheckAnswerResponse:
    """Pure grading — no auth, no graph write. Lets the frontend show instant right/wrong
    feedback (plus the explanation, revealed only now that the student has answered)
    before the student has picked a confidence level."""
    question = _get_question_or_404(bank, uid)
    _validate_selected_index(question, body.selected_index)

    correct_index = question.correct_option_index()
    return CheckAnswerResponse(
        correct=body.selected_index == correct_index,
        correct_index=correct_index,
        explanation=question.explanation,
    )


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
        session_id=body.session_id,
        question_uid=uid,
        selected_index=body.selected_index,
        correct=is_correct,
        confidence=body.confidence.value,
        time_taken_seconds=body.time_taken_seconds,
        topic_tag=question.topic_tag,
    )
    if event_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such session for this student",
        )

    return LogAttemptResponse(event_id=event_id, correct=is_correct)
