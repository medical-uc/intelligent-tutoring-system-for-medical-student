from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.schemas import (
    NudgePreviewItem,
    NudgeResponse,
    NudgeSource,
    SessionCheckResponse,
    SessionResponse,
    StreakResponse,
    StudentLoginRequest,
    StudentProfileResponse,
    StudentRegisterRequest,
    StudentRegisterResponse,
)
from src.flashcards.reviews import count_due_for_review as count_due_flashcards
from src.flashcards.reviews import due_for_review as due_flashcards
from src.quiz.attempts import count_due_for_review as count_due_quiz
from src.quiz.attempts import due_for_review as due_quiz
from src.student_kg.enrollment import enroll_student, get_student_by_id
from src.student_kg.session import (
    create_session,
    find_student_id_by_number,
    revoke_session,
)
from src.student_kg.streak import current_streak, week_activity

router = APIRouter(prefix="/students", tags=["students"])
_bearer_scheme = HTTPBearer()


@router.post("/register", response_model=StudentRegisterResponse, status_code=201)
def register_student(
    body: StudentRegisterRequest,
    driver: Driver = Depends(get_driver),
) -> StudentRegisterResponse:
    student_id = enroll_student(
        driver, body.full_name, body.student_number, body.academic_year
    )
    session = create_session(driver, student_id)
    return StudentRegisterResponse(
        student_id=student_id,
        token=session.token,
        expires_at=session.expires_at,
    )


@router.post("/login", response_model=SessionResponse)
def login_student(
    body: StudentLoginRequest,
    driver: Driver = Depends(get_driver),
) -> SessionResponse:
    student_id = find_student_id_by_number(driver, body.student_number)
    if student_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no student with this student_number",
        )
    session = create_session(driver, student_id)
    return SessionResponse(
        student_id=student_id, token=session.token, expires_at=session.expires_at
    )


@router.post("/logout", status_code=204)
def logout_student(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    driver: Driver = Depends(get_driver),
) -> None:
    revoke_session(driver, credentials.credentials)


@router.get("/me", response_model=SessionCheckResponse)
def check_session(
    student_id: str = Depends(get_current_student_id),
) -> SessionCheckResponse:
    return SessionCheckResponse(authenticated=True, student_id=student_id)


@router.get("/me/profile", response_model=StudentProfileResponse)
def get_my_profile(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> StudentProfileResponse:
    profile = get_student_by_id(driver, student_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="student not found"
        )
    return StudentProfileResponse(
        student_id=profile.student_id,
        full_name=profile.full_name,
        student_number=profile.student_number,
        academic_year=profile.academic_year,
        enrolled_at=profile.enrolled_at,
    )


@router.get("/me/streak", response_model=StreakResponse)
def get_my_streak(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> StreakResponse:
    """Consecutive UTC days with a quiz session or flashcard review, walking back from
    today, plus this UTC calendar week's Monday-Sunday activity — see
    src/student_kg/streak.py for exact semantics."""
    return StreakResponse(
        current_streak=current_streak(driver, student_id),
        week_activity=week_activity(driver, student_id),
    )


@router.get("/me/nudge", response_model=NudgeResponse)
def get_my_nudge(
    student_id: str = Depends(get_current_student_id),
    driver: Driver = Depends(get_driver),
) -> NudgeResponse:
    """Dashboard due-for-review nudge: how many quiz questions and flashcards this
    student's spaced-repetition schedule says are due right now, plus whichever single
    item is due soonest across both. Counts only (no full queues) — pair with
    /quiz/review/due or /flashcards/review/due once the student acts on the nudge."""
    quiz_count = count_due_quiz(driver, student_id)
    flashcard_count = count_due_flashcards(driver, student_id)

    soonest: NudgePreviewItem | None = None
    quiz_top = due_quiz(driver, student_id, limit=1)
    flashcard_top = due_flashcards(driver, student_id, limit=1)

    candidates = []
    if quiz_top:
        candidates.append((NudgeSource.QUIZ, quiz_top[0]))
    if flashcard_top:
        candidates.append((NudgeSource.FLASHCARD, flashcard_top[0]))
    if candidates:
        source, item = min(candidates, key=lambda c: c[1]["next_review_at"])
        soonest = NudgePreviewItem(
            source=source,
            question_uid=item["question_uid"],
            next_review_at=item["next_review_at"].to_native(),
        )

    return NudgeResponse(
        quiz_due_count=quiz_count,
        flashcard_due_count=flashcard_count,
        total_due_count=quiz_count + flashcard_count,
        soonest_due=soonest,
    )
