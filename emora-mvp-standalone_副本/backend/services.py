import logging
from collections.abc import Callable

from pydantic import ValidationError

from backend.agent import EmoraAgent, EmoraAgentGraph
from backend.contracts import (
    AgentAction,
    AgentDecision,
    AnalysisResponse,
    AnalysisStatus,
    DEFAULT_REASONING_EFFORT,
    QwenSafetyCategory,
    QwenSafetyLevel,
    ReasoningEffort,
)
from backend.provider import PUBLIC_ANALYSIS_PROMPT, ProviderError, PublicAnalysisBrief
from backend.safety import GuardProtocolError, GuardUnavailableError

logger = logging.getLogger(__name__)


class BusinessError(Exception):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code


def validate_input_length(value: str, max_length: int) -> None:
    if len(value) > max_length:
        raise BusinessError(422, "input_too_long")


def _response(record: dict) -> AnalysisResponse:
    return AnalysisResponse(
        id=record["id"], original_input=record["original_input"],
        deep_thinking=record.get("deep_thinking", False),
        reasoning_effort=record.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
        status=AnalysisStatus(record["status"]),
        clarification_question=record["clarification_question"], result=record["result"],
        safety_level=record["input_safety_level"], safety_categories=record["safety_categories"],
        error_code=record["error_code"], created_at=record["created_at"], updated_at=record["updated_at"],
    )


def _result_text(decision: AgentDecision) -> str:
    if decision.action == AgentAction.ASK_CLARIFICATION:
        return decision.clarification_question or ""
    return decision.result.model_dump_json(exclude_none=True)


class AnalysisService:
    def __init__(self, *, database, provider, guard, settings, graph=None):
        self.database = database
        self.provider = provider
        self.guard = guard
        self.settings = settings
        self.graph = graph or EmoraAgentGraph(EmoraAgent(provider))

    async def _input_guard(self, context: str):
        try:
            result = await self.guard.check_input(context)
        except (GuardUnavailableError, GuardProtocolError) as exc:
            raise BusinessError(503, "safety_check_unavailable") from exc
        if result.safety == QwenSafetyLevel.UNSAFE:
            return result
        return result

    async def _run(self, *, original_input: str, question=None, answer=None, round_number=0,
                   deep_thinking: bool = False,
                   reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
                   progress: Callable[[str], None] | None = None,
                   analysis_summary: Callable[[str], None] | None = None):
        context = original_input
        if question and answer is not None:
            context = f"Original description:\n{original_input}\n\nClarification question:\n{question}\n\nUser answer:\n{answer}"
        if progress:
            progress("input_safety_check")
        guard = await self._input_guard(context)
        if guard.safety == QwenSafetyLevel.UNSAFE:
            return None, guard
        if progress:
            progress(f"model_reasoning_{reasoning_effort}")
        try:
            summary = None
            if deep_thinking:
                if progress:
                    progress("deep_analysis")
                brief_payload = await self.provider.complete(
                    {"conversation": context}, PublicAnalysisBrief,
                    reasoning_effort=reasoning_effort,
                    system_prompt=PUBLIC_ANALYSIS_PROMPT,
                    format_instruction=(
                        "Return exactly one JSON object matching the schema. "
                        "Do not include Markdown fences or text outside the object."
                    ),
                )
                summary = PublicAnalysisBrief.model_validate(brief_payload).summary.strip()
                if not summary:
                    raise BusinessError(503, "analysis_unavailable")
                if progress:
                    progress("analysis_summary_safety_check")
                try:
                    summary_guard = await self.guard.check_output(context, summary)
                except (GuardUnavailableError, GuardProtocolError) as exc:
                    raise BusinessError(503, "safety_check_unavailable") from exc
                if summary_guard.safety != QwenSafetyLevel.SAFE:
                    raise BusinessError(503, "output_rejected")
                if analysis_summary:
                    analysis_summary(summary)
            if progress:
                progress("response_generation")
            payload = await self.graph.ainvoke(
                original_input=original_input,
                clarification_question=question,
                clarification_answer=answer,
                clarification_round=round_number,
                deep_thinking=deep_thinking,
                reasoning_effort=reasoning_effort,
                analysis_summary=summary,
            )
            decision = AgentDecision.model_validate(payload)
        except BusinessError:
            raise
        except (ProviderError, ValidationError, ValueError) as exc:
            raise BusinessError(503, "analysis_unavailable") from exc
        except Exception as exc:
            raise BusinessError(503, "analysis_unavailable") from exc

        if round_number == 1 and decision.action == AgentAction.ASK_CLARIFICATION:
            raise BusinessError(500, "clarification_limit_exceeded")
        if decision.action == AgentAction.COMPLETE:
            result = decision.result
            if not all([
                result.summary.strip(), result.conflict.strip(), result.user_needs.strip(),
                result.next_step.strip(), *[value.strip() for value in result.emotions],
                *[value.strip() for value in result.suggestions],
            ]):
                raise BusinessError(500, "invalid_analysis_result")
        try:
            if progress:
                progress("output_safety_check")
            output = await self.guard.check_output(context, _result_text(decision))
        except (GuardUnavailableError, GuardProtocolError) as exc:
            raise BusinessError(503, "safety_check_unavailable") from exc
        if output.safety != QwenSafetyLevel.SAFE:
            raise BusinessError(503, "output_rejected")
        return decision, guard

    async def create(self, text: str, *, deep_thinking: bool = False,
                     reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
                     progress: Callable[[str], None] | None = None,
                     analysis_summary: Callable[[str], None] | None = None) -> AnalysisResponse:
        validate_input_length(text, self.settings.max_input_length)
        try:
            decision, guard = await self._run(original_input=text, deep_thinking=deep_thinking,
                                              reasoning_effort=reasoning_effort,
                                              progress=progress, analysis_summary=analysis_summary)
        except BusinessError as exc:
            logger.warning("analysis_failed code=%s", exc.code)
            if progress:
                progress("result_persistence")
            record = self.database.create(text, "failed", error_code=exc.code,
                                          deep_thinking=deep_thinking, reasoning_effort=reasoning_effort)
            return _response(record)
        if progress:
            progress("result_persistence")
        if decision is None:
            urgent = any(category in guard.categories for category in (
                QwenSafetyCategory.SUICIDE_SELF_HARM, QwenSafetyCategory.VIOLENT,
            ))
            record = self.database.create(text, "blocked", guard=guard, error_code="unsafe_input",
                                          deep_thinking=deep_thinking, reasoning_effort=reasoning_effort)
            response = _response(record)
            if urgent:
                response.error_code = "unsafe_input_seek_support"
            return response
        if decision.action == AgentAction.ASK_CLARIFICATION:
            record = self.database.create(
                text, "waiting_clarification", guard=guard,
                clarification_question=decision.clarification_question,
                deep_thinking=deep_thinking,
                reasoning_effort=reasoning_effort,
            )
        else:
            record = self.database.create(text, "completed", guard=guard,
                                          result=decision.result.model_dump(mode="json"),
                                          deep_thinking=deep_thinking,
                                          reasoning_effort=reasoning_effort)
        return _response(record)

    async def continue_with_answer(self, analysis_id: str, answer: str, *,
                                   deep_thinking: bool | None = None,
                                   reasoning_effort: ReasoningEffort | None = None,
                                   progress: Callable[[str], None] | None = None,
                                   analysis_summary: Callable[[str], None] | None = None) -> AnalysisResponse:
        validate_input_length(answer, self.settings.max_input_length)
        previous = self.database.get(analysis_id)
        if previous is None:
            raise BusinessError(404, "analysis_not_found")
        if previous["status"] != AnalysisStatus.WAITING_CLARIFICATION.value:
            raise BusinessError(422, "analysis_not_waiting_clarification")
        deep_thinking = previous["deep_thinking"] if deep_thinking is None else deep_thinking
        reasoning_effort = previous.get("reasoning_effort", DEFAULT_REASONING_EFFORT) \
            if reasoning_effort is None else reasoning_effort
        try:
            decision, guard = await self._run(
                original_input=previous["original_input"],
                question=previous["clarification_question"], answer=answer, round_number=1,
                deep_thinking=deep_thinking, progress=progress, analysis_summary=analysis_summary,
                reasoning_effort=reasoning_effort,
            )
        except BusinessError as exc:
            logger.warning("analysis_failed code=%s", exc.code)
            if exc.code != "analysis_not_found" and exc.code != "analysis_not_waiting_clarification":
                if progress:
                    progress("result_persistence")
                record = self.database.update(analysis_id, status="failed", clarification_answer=answer,
                                              error_code=exc.code, deep_thinking=deep_thinking,
                                              reasoning_effort=reasoning_effort)
                return _response(record)
            raise
        if progress:
            progress("result_persistence")
        if decision is None:
            urgent = any(category in guard.categories for category in (
                QwenSafetyCategory.SUICIDE_SELF_HARM, QwenSafetyCategory.VIOLENT,
            ))
            record = self.database.update(analysis_id, status="blocked", guard=guard,
                                          clarification_answer=answer,
                                          error_code="unsafe_input_seek_support" if urgent else "unsafe_input",
                                          deep_thinking=deep_thinking, reasoning_effort=reasoning_effort)
        else:
            record = self.database.update(analysis_id, status="completed", guard=guard,
                                          clarification_answer=answer,
                                          result=decision.result.model_dump(mode="json"),
                                          deep_thinking=deep_thinking,
                                          reasoning_effort=reasoning_effort)
        return _response(record)

    def get(self, analysis_id: str) -> AnalysisResponse:
        record = self.database.get(analysis_id)
        if record is None:
            raise BusinessError(404, "analysis_not_found")
        return _response(record)

    def list(self) -> list[AnalysisResponse]:
        return [_response(record) for record in self.database.list()]

    async def close(self):
        return None
