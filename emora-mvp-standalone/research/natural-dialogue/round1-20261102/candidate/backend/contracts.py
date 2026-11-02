from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_INPUT_LENGTH = 10_000
REASONING_EFFORTS = ("none", "low", "medium", "high", "xhigh", "max")
ReasoningEffort = Literal["none", "low", "medium", "high", "xhigh", "max"]
DEFAULT_REASONING_EFFORT: ReasoningEffort = "medium"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisStatus(str, Enum):
    WAITING_CLARIFICATION = "waiting_clarification"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class AnalysisRequest(Contract):
    text: str = Field(min_length=1, max_length=MAX_INPUT_LENGTH)
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
    session_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ClarificationRequest(Contract):
    answer: str = Field(min_length=1, max_length=MAX_INPUT_LENGTH)
    reasoning_effort: ReasoningEffort | None = None

    @field_validator("answer")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer must not be blank")
        return value


class AnalysisResult(Contract):
    summary: str = Field(min_length=1)
    emotions: list[str] = Field(min_length=1)
    conflict: str = Field(min_length=1)
    user_needs: str = Field(min_length=1)
    suggestions: list[str] = Field(min_length=1)
    next_step: str = Field(min_length=1)
    safety_note: str | None = None


class SessionTitle(Contract):
    title: str = Field(min_length=1, max_length=24)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        value = " ".join(value.split()).strip("\"'`“”‘’")
        if not value:
            raise ValueError("title must not be blank")
        return value[:24]


class SessionTitleResponse(Contract):
    session_id: str
    title: str


class QwenSafetyLevel(str, Enum):
    SAFE = "Safe"
    CONTROVERSIAL = "Controversial"
    UNSAFE = "Unsafe"


class QwenSafetyCategory(str, Enum):
    VIOLENT = "Violent"
    NON_VIOLENT_ILLEGAL_ACTS = "Non-violent Illegal Acts"
    SEXUAL_CONTENT_OR_ACTS = "Sexual Content or Sexual Acts"
    PII = "PII"
    SUICIDE_SELF_HARM = "Suicide & Self-Harm"
    UNETHICAL_ACTS = "Unethical Acts"
    POLITICALLY_SENSITIVE_TOPICS = "Politically Sensitive Topics"
    COPYRIGHT_VIOLATION = "Copyright Violation"
    JAILBREAK = "Jailbreak"
    NONE = "None"


class QwenSafetyPhase(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


class Qwen3GuardResult(Contract):
    phase: QwenSafetyPhase
    safety: QwenSafetyLevel
    categories: list[QwenSafetyCategory]
    refusal: Literal["Yes", "No"] | None = None
    model_id: str


class AgentDecision(Contract):
    """One conversational turn, including any question or advice in the reply itself."""

    reply: str = Field(min_length=1)


class AnalysisResponse(Contract):
    id: str
    original_input: str
    session_id: str | None = None
    turn_index: int = 0
    clarification_answer: str | None = None
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
    status: AnalysisStatus
    clarification_question: str | None = None
    result: AgentDecision | AnalysisResult | None = None
    safety_level: QwenSafetyLevel | None = None
    safety_categories: list[QwenSafetyCategory] = Field(default_factory=list)
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class AnalysisListResponse(Contract):
    items: list[AnalysisResponse]


class SessionHistoryResponse(Contract):
    session_id: str
    items: list[AnalysisResponse]
    next_before: int | None = None


class SessionSummary(Contract):
    session_id: str
    title: str
    status: AnalysisStatus
    created_at: datetime
    updated_at: datetime
    pinned: bool = False
    archived: bool = False


class SessionListResponse(Contract):
    items: list[SessionSummary]


class SessionUpdateRequest(Contract):
    pinned: bool | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def require_session_change(self):
        if self.pinned is None and self.archived is None:
            raise ValueError("at least one session state field is required")
        return self


class HealthResponse(Contract):
    status: str


class RuntimeInfoResponse(Contract):
    model_name: str
    reasoning_efforts: list[ReasoningEffort]
    default_reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
    response_format: str = "json_object"
    schema_validation: str = "application"
    reasoning_effort_verification: str = "accepted_effect_unverified"
