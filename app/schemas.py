from pydantic import BaseModel, Field


class StudentRegisterRequest(BaseModel):
    name: str = Field(min_length=1)
    academic_year: int = Field(ge=1, le=6)


class StudentRegisterResponse(BaseModel):
    student_id: str
    event_id: str
