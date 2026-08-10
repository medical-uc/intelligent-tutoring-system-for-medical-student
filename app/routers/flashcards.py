from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.schemas import (
    DueFlashcardItem,
    DueFlashcardResponse,
    EndFlashcardSessionResponse,
    FlashcardHistoryItem,
    FlashcardHistoryResponse,
    FlashcardOut,
    FlashcardRevealResponse,
    FlashcardReviewHistoryItem,
    FlashcardReviewHistoryResponse,
    FlashcardSessionHistoryItem,
    FlashcardSessionHistoryResponse,
    LogFlashcardReviewRequest,
    LogFlashcardReviewResponse,
    StartFlashcardSessionRequest,
    StartFlashcardSessionResponse,
)
from src.flashcards.cards import (
    Flashcard,
    all_flashcards,
    flashcards_for_topic,
    get_flashcard,
)
from src.flashcards.reviews import (
    due_for_review,
    history_for_student,
    record_review,
    review_history,
)
from src.flashcards.sessions import (
    DEFAULT_SESSION_SIZE,
    cancel_session,
    end_session,
)
from src.flashcards.sessions import history_for_student as session_history_for_student
from src.flashcards.sessions import start_session
from src.quiz.bank import QuestionBank, load_question_bank

router = APIRouter(prefix="/flashcards", tags=["flashcards"])


def _get_bank() -> QuestionBank:
    return load_question_bank()


def _to_flashcard_out(card: Flashcard) -> FlashcardOut:
    return FlashcardOut(
        uid=card.uid,
        front=card.front,
        topic_tag=card.topic_tag,
        difficulty=card.difficulty,
    )


@router.get("/cards", response_model=list[FlashcardOut])
def get_all_cards(
    bank: QuestionBank = Depends(_get_bank),
) -> list[FlashcardOut]:
    return [_to_flashcard_out(c) for c in all_flashcards(bank=bank)]


@router.get("/topics/{topic_path:path}/cards", response_model=list[FlashcardOut])
def get_cards_for_topic(
    topic_path: str,
    bank: QuestionBank = Depends(_get_bank),
) -> list[FlashcardOut]:
    cards = flashcards_for_topic(topic_path, bank=bank)
    if not cards:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no cards for this topic"
        )
    return [_to_flashcard_out(c) for c in cards]


@router.post("/sessions", response_model=StartFlashcardSessionResponse)
def start_flashcard_session(
    body: StartFlashcardSessionRequest = StartFlashcardSessionRequest(),
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> StartFlashcardSessionResponse:
    """Starts an Anki-style batch of up to `size` cards (10 by default) drawn from the
    whole deck: due-for-review cards first (soonest-due), then never-reviewed cards top up
    the rest. The returned card_uids is the fixed batch for this session — walk it with
    /cards/{uid}/reveal and /cards/{uid}/log same as before, then call /sessions/{id}/end."""
    session = start_session(driver, student_id=student_id, bank=bank, size=body.size)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no cards available"
        )
    return StartFlashcardSessionResponse(**session)


@router.post(
    "/topics/{topic_path:path}/sessions", response_model=StartFlashcardSessionResponse
)
def start_flashcard_session_for_topic(
    topic_path: str,
    body: StartFlashcardSessionRequest = StartFlashcardSessionRequest(),
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> StartFlashcardSessionResponse:
    """Same as /sessions but scoped to one topic's cards."""
    session = start_session(
        driver, student_id=student_id, bank=bank, topic_path=topic_path, size=body.size
    )
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no cards for this topic"
        )
    return StartFlashcardSessionResponse(**session)


@router.post("/sessions/{session_id}/end", response_model=EndFlashcardSessionResponse)
def end_flashcard_session(
    session_id: str,
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> EndFlashcardSessionResponse:
    """Call once the student has walked through (or given up on) the batch. Counts how many
    of the session's card_uids got a rating logged during the session window — ratings
    themselves already happened via /cards/{uid}/log independently of the session."""
    summary = end_session(driver, student_id=student_id, session_id=session_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such session for this student",
        )
    return EndFlashcardSessionResponse(**summary)


@router.post(
    "/sessions/{session_id}/cancel", response_model=EndFlashcardSessionResponse
)
def cancel_flashcard_session(
    session_id: str,
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> EndFlashcardSessionResponse:
    """Call instead of /end when the student abandons the batch partway through. Same
    aggregation as /end, but marks the session "cancelled" instead of "completed"."""
    summary = cancel_session(driver, student_id=student_id, session_id=session_id)
    if summary is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such in-progress session for this student",
        )
    return EndFlashcardSessionResponse(**summary)


@router.get("/sessions/history", response_model=FlashcardSessionHistoryResponse)
def get_session_history(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> FlashcardSessionHistoryResponse:
    """Completed/cancelled flashcard sessions for the current student, most recent first."""
    sessions = session_history_for_student(driver, student_id=student_id)
    return FlashcardSessionHistoryResponse(
        items=[
            FlashcardSessionHistoryItem(
                session_id=s["session_id"],
                topic_path=s["topic_path"],
                status=s["status"],
                card_count=s["card_count"],
                reviewed_count=s["reviewed_count"],
                duration_seconds=s["duration_seconds"],
                started_at=s["started_at"].to_native(),
                ended_at=s["ended_at"].to_native(),
            )
            for s in sessions
        ]
    )


@router.post("/cards/{uid}/reveal", response_model=FlashcardRevealResponse)
def reveal_card(
    uid: str,
    bank: QuestionBank = Depends(_get_bank),
) -> FlashcardRevealResponse:
    """Pure lookup — no auth, no graph write. Called when the student flips the card,
    after they've already self-assessed recall in their head."""
    card = get_flashcard(uid, bank=bank)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such card"
        )
    return FlashcardRevealResponse(
        uid=card.uid, back=card.back, explanation=card.explanation
    )


@router.post("/cards/{uid}/log", response_model=LogFlashcardReviewResponse)
def log_review(
    uid: str,
    body: LogFlashcardReviewRequest,
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> LogFlashcardReviewResponse:
    """Called once the student has flipped the card and rated their own recall. Updates
    the card's Flashcard-node spaced-repetition schedule independently of any quiz mastery,
    and links the review to body.session_id via :HAS_ANSWER (see src.flashcards.sessions).
    """
    card = get_flashcard(uid, bank=bank)
    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no such card"
        )

    result = record_review(
        driver,
        student_id=student_id,
        session_id=body.session_id,
        question_uid=uid,
        rating=body.rating.value,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no such session for this student",
        )

    return LogFlashcardReviewResponse(
        event_id=result["event_id"],
        streak=result["streak"],
        interval_days=result["interval_days"],
        next_review_at=result["next_review_at"].to_native(),
        energy_awarded=result["energy_awarded"],
        energy_balance=result["energy_balance"],
    )


@router.get("/cards/{uid}/history", response_model=FlashcardReviewHistoryResponse)
def get_review_history(
    uid: str,
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> FlashcardReviewHistoryResponse:
    """Every Again/Hard/Good/Easy rating this student has given this card, oldest first —
    the raw trail behind the current streak/interval on its Flashcard node."""
    items = review_history(driver, student_id=student_id, question_uid=uid)
    return FlashcardReviewHistoryResponse(
        items=[
            FlashcardReviewHistoryItem(
                event_id=r["event_id"], rating=r["rating"], ts=r["ts"].to_native()
            )
            for r in items
        ]
    )


@router.get("/history", response_model=FlashcardHistoryResponse)
def get_history(
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> FlashcardHistoryResponse:
    """Every flashcard review this student has ever logged, most recent first. A flat feed
    (one row per rating) rather than session-grouped rows — ratings are recorded
    independently of any FlashcardSession (see src.flashcards.reviews.history_for_student);
    pair with GET /sessions/history for the batch-level view. Rows whose question no
    longer exists in the bank are skipped rather than erroring,
    since the bank can be regenerated independently of logged history."""
    reviews = history_for_student(driver, student_id=student_id)
    items = []
    for r in reviews:
        card = get_flashcard(r["question_uid"], bank=bank)
        if card is None:
            continue
        items.append(
            FlashcardHistoryItem(
                event_id=r["event_id"],
                question_uid=r["question_uid"],
                front=card.front,
                topic_path=" > ".join(card.topic_tag),
                rating=r["rating"],
                ts=r["ts"].to_native(),
            )
        )
    return FlashcardHistoryResponse(items=items)


@router.get("/review/due", response_model=DueFlashcardResponse)
def get_due_for_review(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> DueFlashcardResponse:
    """Cards this student has reviewed before whose schedule says they're due again now,
    soonest-due first. A card never reviewed yet has no schedule and never appears here —
    pair with /topics/{path}/cards for "drill this topic fresh" flows."""
    items = due_for_review(driver, student_id=student_id)
    return DueFlashcardResponse(
        items=[
            DueFlashcardItem(
                question_uid=r["question_uid"],
                streak=r["streak"],
                interval_days=r["interval_days"],
                last_reviewed_at=r["last_reviewed_at"].to_native(),
                next_review_at=r["next_review_at"].to_native(),
            )
            for r in items
        ]
    )
