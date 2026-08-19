from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.openapi import error_responses
from app.schemas import (
    BKTParamsResponse,
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
    MasteryItem,
    MasteryResponse,
    OptionOut,
    QuestionOut,
    StartBatchQuizSessionRequest,
    StartBatchQuizSessionResponse,
    StartSessionResponse,
    UpdateMasteryRequest,
    UpdateMasteryResponse,
)
from src.quiz.attempts import due_for_review, record_attempt
from src.quiz.bank import Question, QuestionBank, load_question_bank
from src.quiz.bkt_fit import get_fitted_params
from src.quiz.mastery import mastery_for_student, upsert_mastery
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


@router.get(
    "/questions", response_model=list[QuestionOut], responses=error_responses(401)
)
def get_all_questions(bank: QuestionBank = Depends(_get_bank)) -> list[QuestionOut]:
    """The whole question bank, unscoped — pairs with POST /sessions (the cross-topic
    batch session), same way GET /flashcards/cards pairs with POST /flashcards/sessions:
    the client fetches everything once, builds a uid -> question lookup, and filters
    against a session's question_uids locally rather than fetching per-topic."""
    return [_to_question_out(q) for q in bank.all()]


@router.post(
    "/sessions",
    response_model=StartBatchQuizSessionResponse,
    responses=error_responses(401, 404),
)
def start_batch_quiz_session(
    body: StartBatchQuizSessionRequest = StartBatchQuizSessionRequest(),
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> StartBatchQuizSessionResponse:
    """Starts a due-first batch of up to `size` questions (10 by default) drawn from the
    whole bank: due-for-review questions first (soonest-due), then never-answered
    questions top up the rest. The returned question_uids is the fixed batch for this
    session — walk it with /questions/{uid}/check and /questions/{uid}/log same as a
    topic run, then call /sessions/{id}/end."""
    session = start_session(driver, student_id=student_id, bank=bank, size=body.size)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no questions available"
        )
    return StartBatchQuizSessionResponse(**session)


@router.post(
    "/topics/{topic_path:path}/sessions",
    response_model=StartSessionResponse,
    responses=error_responses(401, 404),
)
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
    session = start_session(driver, student_id=student_id, topic_path=topic_path)
    return StartSessionResponse(session_id=session["session_id"])


@router.post(
    "/sessions/{session_id}/end",
    response_model=EndSessionResponse,
    responses=error_responses(401, 404),
)
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


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=CancelSessionResponse,
    responses=error_responses(401, 404),
)
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


@router.get(
    "/history", response_model=HistoryResponse, responses=error_responses(401)
)
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


@router.get(
    "/review/due", response_model=DueReviewResponse, responses=error_responses(401)
)
def get_due_for_review(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
    bank: QuestionBank = Depends(_get_bank),
) -> DueReviewResponse:
    """Questions this student has answered before whose spaced-repetition schedule says
    they're due again now, soonest-due first. A question the student has never answered
    has no schedule yet and never appears here — pair with /topics/{path}/questions for
    "answer this for the first time" flows. Rows whose question no longer exists in the
    bank are skipped, same convention as GET /history."""
    items = due_for_review(driver, student_id=student_id)
    result = []
    for r in items:
        question = bank.get(r["question_uid"])
        if question is None:
            continue
        result.append(
            DueReviewItem(
                question_uid=r["question_uid"],
                topic_path=question.topic_path,
                streak=r["streak"],
                interval_days=r["interval_days"],
                last_reviewed_at=r["last_reviewed_at"].to_native(),
                next_review_at=r["next_review_at"].to_native(),
            )
        )
    return DueReviewResponse(items=result)


@router.get(
    "/mastery", response_model=MasteryResponse, responses=error_responses(401)
)
def get_mastery(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> MasteryResponse:
    """This student's current per-topic mastery (p_know), weakest topic first — computed
    on-device by the frontend's own BKT implementation and pushed here via PUT /mastery,
    not computed server-side (see src/quiz/mastery.py). A topic never pushed by the client
    has no :MASTERS edge and doesn't appear here."""
    items = mastery_for_student(driver, student_id=student_id)
    return MasteryResponse(
        items=[
            MasteryItem(
                topic_path=r["topic_path"],
                p_know=r["p_know"],
                updated_at=r["updated_at"].to_native(),
            )
            for r in items
        ]
    )


@router.put(
    "/mastery",
    response_model=UpdateMasteryResponse,
    responses=error_responses(401, 404),
)
def update_mastery(
    body: UpdateMasteryRequest,
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> UpdateMasteryResponse:
    """Call after a quiz session (or whenever the frontend recomputes its on-device BKT
    model) to persist the resulting p_know per topic — typically every leaf topic touched
    during that session. Overwrites whatever p_know was previously stored for each
    topic_path; the server does no BKT math of its own and applies no bounds/validation to
    p_know, trusting the client's algorithm entirely."""
    updated_count = upsert_mastery(
        driver,
        student_id=student_id,
        items=[item.model_dump() for item in body.items],
    )
    if updated_count is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such student"
        )
    return UpdateMasteryResponse(updated_count=updated_count)


@router.get(
    "/mastery/params", response_model=BKTParamsResponse, responses=error_responses(401)
)
def get_mastery_params(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> BKTParamsResponse:
    """The global BKT emission/transition parameters (p_init, p_transit, p_slip,
    p_guess), EM-fit from every student's QUIZ_ANSWER history pooled together (see
    src/quiz/bkt_fit.py) — not this student's own data alone. The client's on-device BKT
    (BKTStore.swift) fetches and caches these, replacing its own hard-coded defaults, so
    the shared model of "how noisy is a confident vs. guessing answer" stays current as
    real interaction volume grows, while p_know itself stays entirely client-computed.
    fitted_from_defaults is true if there wasn't yet enough data to fit and the original
    hand-picked defaults were returned as-is — the client should still cache and use
    them the same way, just without expecting the improved accuracy a real fit gives."""
    fitted = get_fitted_params(driver)
    return BKTParamsResponse(
        p_init=fitted.p_init,
        p_transit=fitted.p_transit,
        p_slip=fitted.p_slip,
        p_guess=fitted.p_guess,
        n_attempts=fitted.n_attempts,
        n_sequences=fitted.n_sequences,
        fitted_from_defaults=fitted.fitted_from_defaults,
    )


@router.post(
    "/questions/{uid}/check",
    response_model=CheckAnswerResponse,
    responses=error_responses(400, 401, 404),
)
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


@router.post(
    "/questions/{uid}/log",
    response_model=LogAttemptResponse,
    responses=error_responses(400, 401, 404),
)
def log_attempt(
    uid: str,
    body: LogAttemptRequest,
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> LogAttemptResponse:
    """Single write for the whole attempt — called once both selected_index and confidence
    are known, so exactly one InteractionEvent is created (never a partial one).

    next_review_days, if given, is the student's own choice of when to see this question
    again (e.g. "in 3 days" picked on the answer screen) and overrides the streak-computed
    schedule for this question — see src.quiz.attempts.record_attempt."""
    question = _get_question_or_404(bank, uid)
    _validate_selected_index(question, body.selected_index)

    correct_index = question.correct_option_index()
    is_correct = body.selected_index == correct_index

    result = record_attempt(
        driver,
        student_id=student_id,
        session_id=body.session_id,
        question_uid=uid,
        selected_index=body.selected_index,
        correct=is_correct,
        confidence=body.confidence.value,
        time_taken_seconds=body.time_taken_seconds,
        topic_tag=question.topic_tag,
        next_review_days=body.next_review_days,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such session for this student",
        )

    return LogAttemptResponse(
        event_id=result["event_id"],
        correct=is_correct,
        next_review_at=result["next_review_at"].to_native(),
    )
