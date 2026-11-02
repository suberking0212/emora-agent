"""带版本的严格数据契约及分析结果校验。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class InputVersion(Contract):
    input_version_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    modality: Literal["text", "image", "audio"]
    confirmed_text: str | None = None
    confirmation: Literal["pending", "confirmed", "corrected"] = "pending"
    source_asset_ids: list[UUID] = Field(default_factory=list)
    provider: str | None = None
    provider_version: str | None = None


class EvidenceRef(Contract):
    evidence_id: UUID = Field(default_factory=uuid4)
    source_kind: Literal["user_input", "reviewed_reference"]
    input_version_id: UUID | None = None
    source_id: UUID | None = None
    source_version_id: UUID | None = None
    span_start: int = Field(default=0, ge=0)
    span_end: int = Field(default=0, ge=0)
    quoted_text: str


class Claim(Contract):
    claim_id: UUID = Field(default_factory=uuid4)
    kind: Literal["event", "feeling", "user_interpretation", "hypothesis", "unknown"]
    text: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    counter_evidence_ids: list[UUID] = Field(default_factory=list)


class Suggestion(Contract):
    suggestion_id: UUID = Field(default_factory=uuid4)
    text: str
    supporting_claim_ids: list[UUID] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    applicability: str
    limitations: str


class AnalysisCandidate(Contract):
    session_id: UUID
    run_id: UUID
    result_version_id: UUID = Field(default_factory=uuid4)
    input_version_id: UUID
    kind: Literal["initial", "final", "safety"]
    problem_summary: str | None = None
    safety_message: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    user_adjustments: list[str] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    clarification_question: str | None = None
    risk_flags: list[str] = Field(default_factory=list)


class AnalysisResult(AnalysisCandidate):
    status: Literal["validated"]


class AnalysisSession(Contract):
    session_id: UUID
    subject_id: UUID
    input_version_id: UUID | None = None
    active_run_id: UUID | None = None
    allowed_source_ids: list[UUID] = Field(default_factory=list)
    deleted_at: datetime | None = None


class RunRecord(Contract):
    run_id: UUID
    session_id: UUID
    input_version_id: UUID
    request_id: UUID
    thread_id: str
    status: Literal[
        "running", "waiting_clarification", "completed", "failed", "cancelled", "superseded"
    ]
    error_code: str | None = None
    clarification_rounds: int = Field(default=0, ge=0, le=2)
    model_version: str
    prompt_version: str
    graph_version: str
    rules_version: str
    schema_version: str
    runtime_version: str
    python_version: str
    langgraph_version: str
    langchain_core_version: str
    checkpointer_version: str
    checkpoint_compatibility_profile: str
    dependency_lock_hash: str
    workflow_state_schema_version: str
    provider: str
    config_version: str
    credential_ref: str


class RunResponse(Contract):
    run_id: UUID
    session_id: UUID
    input_version_id: UUID
    request_id: UUID
    status: Literal[
        "running", "waiting_clarification", "completed", "failed", "cancelled", "superseded"
    ]
    error_code: str | None = None
    clarification_rounds: int = Field(ge=0, le=2)
    provider: str
    model_version: str
    prompt_version: str
    graph_version: str
    rules_version: str
    schema_version: str
    runtime_version: str


class ImprovementConsent(Contract):
    consent_id: UUID = Field(default_factory=uuid4)
    subject_id: UUID
    session_id: UUID
    purpose: Literal["product_improvement"] = "product_improvement"
    notice_version: str
    status: Literal["denied", "granted", "revoked"] = "denied"
    data_version_ids: list[UUID] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SafetyDecision(Contract):
    _diagnostic_call_id: str | None = PrivateAttr(default=None)
    route: Literal["normal", "safety", "uncertain"]
    risk_flags: list[str] = Field(default_factory=list)


class RetrievedSource(Contract):
    source_id: UUID
    source_version_id: UUID
    source_type: str
    title: str
    excerpt: str
    locator: str | None = None
    evidence_tier: str | None = None
    source_url: str | None = None


class RunEvent(Contract):
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    run_id: UUID
    session_id: UUID
    input_version_id: UUID
    thread_id: str
    step: str
    status: Literal["started", "succeeded", "waiting", "failed", "cancelled"]
    version: str = "stage1.v2"
    provider: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    error_code: str | None = None
    input_summary: str | None = None
    model_version: str
    prompt_version: str
    graph_version: str
    rules_version: str
    schema_version: str
    runtime_version: str
    checkpoint_compatibility_profile: str
    dependency_lock_hash: str

    @model_validator(mode="after")
    def redact_summary(self) -> RunEvent:
        if self.input_summary is not None:
            raise ValueError("input_summary must be null")
        return self


class ContextUnderstanding(Contract):
    claims: list[Claim] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class ModelCallContext(Contract):
    text: str
    session_id: UUID
    run_id: UUID
    input_version_id: UUID
    history: list[AnalysisResult] = Field(default_factory=list)
    clarification_rounds: int = Field(ge=0, le=2)
    clarification_allowed: bool


class SkipResume(Contract):
    request_id: UUID
    skip: Literal[True]


class InputResume(Contract):
    request_id: UUID
    skip: Literal[False]
    input_version_id: UUID


ResumeRequest = Annotated[SkipResume | InputResume, Field(discriminator="skip")]


def _unique(items: list, attr: str) -> None:
    values = [getattr(item, attr) for item in items]
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {attr}")


def validate_result(
    result: AnalysisCandidate,
    source_text: str,
    reference_versions: dict[UUID, tuple[UUID, str]] | None = None,
    allowed_source_ids: set[UUID] | None = None,
) -> AnalysisCandidate:
    """依据用户提交的确切原文，校验引用及结果内部的关联关系。

    引用位置必须与已确认输入版本逐字匹配，防止模型生成看似合理、
    实际却不存在于用户输入中的引文。
    """
    _unique(result.evidence_refs, "evidence_id")
    _unique(result.claims, "claim_id")
    _unique(result.suggestions, "suggestion_id")
    evidence = {ref.evidence_id: ref for ref in result.evidence_refs}
    claims = {claim.claim_id: claim for claim in result.claims}
    for ref in result.evidence_refs:
        if ref.source_kind == "user_input":
            if ref.input_version_id != result.input_version_id:
                raise ValueError("evidence_scope_invalid")
            if ref.source_id is not None or ref.source_version_id is not None:
                raise ValueError("evidence_scope_invalid")
            citation_text = source_text
        elif ref.source_kind == "reviewed_reference":
            if ref.input_version_id is not None or ref.source_id is None or ref.source_version_id is None:
                raise ValueError("evidence_scope_invalid")
            if reference_versions is None or allowed_source_ids is None:
                raise ValueError("evidence_scope_invalid")
            reference = reference_versions.get(ref.source_version_id)
            if reference is None or reference[0] != ref.source_id or ref.source_id not in allowed_source_ids:
                raise ValueError("evidence_scope_invalid")
            citation_text = reference[1]
        else:
            raise ValueError("evidence_scope_invalid")
        if not 0 <= ref.span_start < ref.span_end <= len(citation_text):
            raise ValueError("evidence_span_invalid")
        if citation_text[ref.span_start : ref.span_end] != ref.quoted_text:
            raise ValueError("evidence_span_invalid")
    for claim in result.claims:
        refs = claim.evidence_ids + claim.counter_evidence_ids
        if len(refs) != len(set(refs)):
            raise ValueError("duplicate evidence reference")
        if any(ref_id not in evidence for ref_id in refs):
            raise ValueError("claim_evidence_missing")
        if claim.kind in {"event", "feeling", "user_interpretation"} and not any(
            evidence[ref_id].source_kind == "user_input" for ref_id in claim.evidence_ids
        ):
            raise ValueError("claim_evidence_missing")
        if claim.kind == "hypothesis" and not claim.evidence_ids:
            raise ValueError("claim_evidence_missing")
    for suggestion in result.suggestions:
        if any(cid not in claims for cid in suggestion.supporting_claim_ids):
            raise ValueError("suggestion_claim_missing")
        if any(eid not in evidence for eid in suggestion.evidence_ids):
            raise ValueError("suggestion_evidence_missing")
    if result.kind == "safety":
        if not result.safety_message or not result.risk_flags or result.suggestions:
            raise ValueError("safety_result_invalid")
    elif result.safety_message is not None:
        raise ValueError("safety_result_invalid")
    if result.kind != "initial" and result.clarification_question:
        raise ValueError("clarification_only_initial")
    return result


def validate_result_scope(
    result: AnalysisCandidate,
    input_version: InputVersion,
    session: AnalysisSession,
    run: RunRecord,
    reference_versions: dict[UUID, tuple[UUID, str]] | None = None,
) -> AnalysisCandidate:
    """发布前检查会话未删除、输入已确认，且结果属于当前输入和运行。"""
    if (
        input_version.confirmation not in {"confirmed", "corrected"}
        or not (input_version.confirmed_text or "").strip()
    ):
        raise ValueError("input_unconfirmed")
    if session.deleted_at is not None:
        raise ValueError("not_found")
    if (
        result.session_id != session.session_id
        or result.run_id != run.run_id
        or result.input_version_id != input_version.input_version_id
    ):
        raise ValueError("stale_version")
    if (
        run.session_id != session.session_id
        or run.input_version_id != input_version.input_version_id
        or run.status != "running"
    ):
        raise ValueError("stale_version")
    return validate_result(
        result,
        input_version.confirmed_text or "",
        reference_versions=reference_versions,
        allowed_source_ids=set(session.allowed_source_ids),
    )
