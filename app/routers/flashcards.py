from fastapi import APIRouter, Depends, HTTPException, status
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.schemas import (
    DueFlashcardItem,
    DueFlashcardResponse,
    FlashcardOut,
    FlashcardRevealResponse,
    LogFlashcardReviewRequest,
    LogFlashcardReviewResponse,
)
from src.flashcards.cards import Flashcard, get_flashcard, flashcards_for_topic
from src.flashcards.reviews import due_for_review, record_review
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


@router.get("/topics/{topic_path:path}/cards", response_model=list[FlashcardOut])
def get_cards_for_topic(
    topic_path: str,
    bank: QuestionBank = Depends(_get_bank),
) -> list[FlashcardOut]:
    cards = flashcards_for_topic(topic_path, bank=bank)
    if not cards:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no cards for this topic")
    return [_to_flashcard_out(c) for c in cards]


@router.post("/cards/{uid}/reveal", response_model=FlashcardRevealResponse)
def reveal_card(
    uid: str,
    bank: QuestionBank = Depends(_get_bank),
) -> FlashcardRevealResponse:
    """Pure lookup — no auth, no graph write. Called when the student flips the card,
    after they've already self-assessed recall in their head."""
    card = get_flashcard(uid, bank=bank)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such card")
    return FlashcardRevealResponse(uid=card.uid, back=card.back, explanation=card.explanation)


@router.post("/cards/{uid}/log", response_model=LogFlashcardReviewResponse)
def log_review(
    uid: str,
    body: LogFlashcardReviewRequest,
    student_id: str = Depends(get_current_student_id),
    bank: QuestionBank = Depends(_get_bank),
    driver: Driver = Depends(get_driver),
) -> LogFlashcardReviewResponse:
    """Called once the student has flipped the card and rated their own recall. Updates
    the :CARD_REVIEWED spaced-repetition schedule independently of any quiz mastery."""
    card = get_flashcard(uid, bank=bank)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such card")

    result = record_review(driver, student_id=student_id, question_uid=uid, rating=body.rating.value)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no such student")

    return LogFlashcardReviewResponse(
        event_id=result["event_id"],
        streak=result["streak"],
        interval_days=result["interval_days"],
        next_review_at=result["next_review_at"].to_native(),
    )


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
