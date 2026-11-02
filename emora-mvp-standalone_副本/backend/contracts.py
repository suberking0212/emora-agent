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
    deep_thinking: bool = False
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class ClarificationRequest(Contract):
    answer: str = Field(min_length=1, max_length=MAX_INPUT_LENGTH)
    deep_thinking: bool | None = None
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


class AgentAction(str, Enum):
    ASK_CLARIFICATION = "ask_clarification"
    COMPLETE = "complete"


class AgentDecision(Contract):
    action: AgentAction
    clarification_question: str | None = None
    result: AnalysisResult | None = None

    @model_validator(mode="after")
    def action_matches_payload(self):
        if self.action == AgentAction.ASK_CLARIFICATION:
            if not self.clarification_question or not self.clarification_question.strip() or self.result:
                raise ValueError("invalid clarification decision")
        elif self.result is None or self.clarification_question is not None:
            raise ValueError("invalid completion decision")
        return self


class AnalysisResponse(Contract):
    id: str
    original_input: str
    deep_thinking: bool = False
    reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
    status: AnalysisStatus
    clarification_question: str | None = None
    result: AnalysisResult | None = None
    safety_level: QwenSafetyLevel | None = None
    safety_categories: list[QwenSafetyCategory] = Field(default_factory=list)
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class AnalysisListResponse(Contract):
    items: list[AnalysisResponse]


class HealthResponse(Contract):
    status: str


class RuntimeInfoResponse(Contract):
    model_name: str
    reasoning_efforts: list[ReasoningEffort]
    default_reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT
