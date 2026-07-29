from fastapi import APIRouter, Depends
from neo4j import Driver

from app.dependencies import get_driver
from app.schemas import StudentRegisterRequest, StudentRegisterResponse
from src.student_kg.enrollment import enroll_student

router = APIRouter(prefix="/students", tags=["students"])


@router.post("/register", response_model=StudentRegisterResponse, status_code=201)
def register_student(
    body: StudentRegisterRequest,
    driver: Driver = Depends(get_driver),
) -> StudentRegisterResponse:
    student_id, event_id = enroll_student(driver, body.name, body.academic_year)
    return StudentRegisterResponse(student_id=student_id, event_id=event_id)
