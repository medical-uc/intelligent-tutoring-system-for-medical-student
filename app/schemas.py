from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class StudentRegisterRequest(BaseModel):
    full_name: str = Field(min_length=1)
    student_number: str = Field(min_length=1)
    academic_year: int = Field(ge=1, le=6)


class StudentRegisterResponse(BaseModel):
    student_id: str
    token: str
    expires_at: datetime


class StudentLoginRequest(BaseModel):
    student_number: str = Field(min_length=1)


class SessionResponse(BaseModel):
    student_id: str
    token: str
    expires_at: datetime


class SessionCheckResponse(BaseModel):
    authenticated: bool
    student_id: str


class StudentProfileResponse(BaseModel):
    student_id: str
    full_name: str
    student_number: str
    academic_year: int
    enrolled_at: datetime


class StreakResponse(BaseModel):
    current_streak: int
    week_activity: list[bool] = Field(min_length=7, max_length=7)


class EnergyResponse(BaseModel):
    energy: int


class NudgeSource(str, Enum):
    QUIZ = "quiz"
    FLASHCARD = "flashcard"


class NudgePreviewItem(BaseModel):
    source: NudgeSource
    question_uid: str
    next_review_at: datetime


class NudgeResponse(BaseModel):
    quiz_due_count: int
    flashcard_due_count: int
    total_due_count: int
    soonest_due: NudgePreviewItem | None = None


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


class SubjectTopicOut(BaseModel):
    path: str
    name: str
    question_count: int
    flashcard_count: int


class SubjectOut(BaseModel):
    name: str
    question_count: int
    flashcard_count: int
    topics: list[SubjectTopicOut]


class SubjectListResponse(BaseModel):
    subjects: list[SubjectOut]


class ConfidenceLevel(str, Enum):
    GUESSING = "guessing"
    UNSURE = "unsure"
    CONFIDENT = "confident"


class CheckAnswerRequest(BaseModel):
    selected_index: int = Field(ge=0)


class CheckAnswerResponse(BaseModel):
    correct: bool
    correct_index: int
    explanation: str


class LogAttemptRequest(BaseModel):
    session_id: str
    selected_index: int = Field(ge=0)
    confidence: ConfidenceLevel
    time_taken_seconds: float = Field(ge=0)


class LogAttemptResponse(BaseModel):
    event_id: str
    correct: bool


class StartSessionResponse(BaseModel):
    session_id: str


class EndSessionResponse(BaseModel):
    session_id: str
    question_count: int
    correct_count: int
    duration_seconds: int
    energy_awarded: int
    energy_balance: int


class CancelSessionResponse(BaseModel):
    session_id: str
    question_count: int
    correct_count: int
    duration_seconds: int


class HistoryItem(BaseModel):
    session_id: str
    topic_path: str
    status: str
    question_count: int
    correct_count: int
    score_percent: int
    duration_seconds: int
    started_at: datetime
    ended_at: datetime


class HistoryResponse(BaseModel):
    items: list[HistoryItem]


class DueReviewItem(BaseModel):
    question_uid: str
    streak: int
    interval_days: int
    last_reviewed_at: datetime
    next_review_at: datetime


class DueReviewResponse(BaseModel):
    items: list[DueReviewItem]


class FlashcardOut(BaseModel):
    """Front only carries the stem — back is withheld until the student flips the card,
    same as options are withheld from quiz questions until /check."""

    uid: str
    front: str
    topic_tag: list[str]
    difficulty: int


class FlashcardRevealResponse(BaseModel):
    uid: str
    back: str
    explanation: str


class FlashcardRating(str, Enum):
    AGAIN = "again"
    HARD = "hard"
    GOOD = "good"
    EASY = "easy"


class LogFlashcardReviewRequest(BaseModel):
    rating: FlashcardRating


class LogFlashcardReviewResponse(BaseModel):
    event_id: str
    streak: int
    interval_days: int
    next_review_at: datetime
    energy_awarded: int
    energy_balance: int


class DueFlashcardItem(BaseModel):
    question_uid: str
    streak: int
    interval_days: int
    last_reviewed_at: datetime
    next_review_at: datetime


class DueFlashcardResponse(BaseModel):
    items: list[DueFlashcardItem]


class FlashcardReviewHistoryItem(BaseModel):
    event_id: str
    rating: FlashcardRating
    ts: datetime


class FlashcardReviewHistoryResponse(BaseModel):
    items: list[FlashcardReviewHistoryItem]


class FlashcardHistoryItem(BaseModel):
    event_id: str
    question_uid: str
    front: str
    topic_path: str
    rating: FlashcardRating
    ts: datetime


class FlashcardHistoryResponse(BaseModel):
    items: list[FlashcardHistoryItem]
