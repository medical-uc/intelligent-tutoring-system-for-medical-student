from datetime import datetime

from pydantic import BaseModel, Field


class StudentRegisterRequest(BaseModel):
    full_name: str = Field(min_length=1)
    student_number: str = Field(min_length=1)
    academic_year: int = Field(ge=1, le=6)


class StudentRegisterResponse(BaseModel):
    student_id: str
    event_id: str
    token: str
    expires_at: datetime


class StudentLoginRequest(BaseModel):
    student_number: str = Field(min_length=1)


class SessionResponse(BaseModel):
    student_id: str
    token: str
    expires_at: datetime


class OptionOut(BaseModel):
    """Client-facing option — no `correct` flag. Position doubles as the answer id."""

    index: int
    text: str


class QuestionOut(BaseModel):
    uid: str
    stem: str
    options: list[OptionOut]
    topic_tag: list[str]
    difficulty: int


class TopicListResponse(BaseModel):
    topics: list[str]


class AnswerSubmitRequest(BaseModel):
    selected_index: int = Field(ge=0)


class AnswerSubmitResponse(BaseModel):
    correct: bool
    correct_index: int
