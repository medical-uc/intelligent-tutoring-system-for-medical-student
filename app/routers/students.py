from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from neo4j import Driver

from app.dependencies import get_current_student_id, get_driver
from app.schemas import (
    SessionCheckResponse,
    SessionResponse,
    StudentLoginRequest,
    StudentProfileResponse,
    StudentRegisterRequest,
    StudentRegisterResponse,
)
from src.student_kg.enrollment import enroll_student, get_student_by_id
from src.student_kg.session import (
    create_session,
    find_student_id_by_number,
    revoke_session,
)

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
