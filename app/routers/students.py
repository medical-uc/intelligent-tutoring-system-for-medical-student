from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from neo4j import Driver

from app.dependencies import get_driver
from app.schemas import SessionResponse, StudentLoginRequest, StudentRegisterRequest, StudentRegisterResponse
from src.student_kg.enrollment import enroll_student
from src.student_kg.session import create_session, find_student_id_by_number, revoke_session

router = APIRouter(prefix="/students", tags=["students"])
_bearer_scheme = HTTPBearer()


@router.post("/register", response_model=StudentRegisterResponse, status_code=201)
def register_student(
    body: StudentRegisterRequest,
    driver: Driver = Depends(get_driver),
) -> StudentRegisterResponse:
    student_id = enroll_student(driver, body.full_name, body.student_number, body.academic_year)
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="no student with this student_number")
    session = create_session(driver, student_id)
    return SessionResponse(student_id=student_id, token=session.token, expires_at=session.expires_at)


@router.post("/logout", status_code=204)
def logout_student(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
    driver: Driver = Depends(get_driver),
) -> None:
    revoke_session(driver, credentials.credentials)
