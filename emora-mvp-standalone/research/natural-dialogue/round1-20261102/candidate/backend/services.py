import asyncio
import logging
import time
import json
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import uuid4
from collections.abc import Callable

from pydantic import ValidationError

from backend.agent import EmoraAgent, EmoraAgentGraph
from backend.contracts import (
    AgentDecision,
    AnalysisResponse,
    AnalysisStatus,
    DEFAULT_REASONING_EFFORT,
    QwenSafetyCategory,
    QwenSafetyLevel,
    ReasoningEffort,
)
from backend.provider import (
    JSONPath,
    ProviderError,
    provider_metrics_var,
)
from backend.safety import GuardProtocolError, GuardUnavailableError
from backend.logging_config import request_id_var, session_id_var
from backend.storage import SessionConflict
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)
validation_var: ContextVar[dict | None] = ContextVar("validation", default=None)


def _validation(stage, status):
    if (state := validation_var.get()) is not None:
        state[stage] = status


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
        session_id=record.get("session_id"), turn_index=record.get("turn_index", 0),
        clarification_answer=record.get("clarification_answer"),
        reasoning_effort=record.get("reasoning_effort", DEFAULT_REASONING_EFFORT),
        status=AnalysisStatus(record["status"]),
        clarification_question=record["clarification_question"], result=record["result"],
        safety_level=record["input_safety_level"], safety_categories=record["safety_categories"],
        error_code=record["error_code"], created_at=record["created_at"], updated_at=record["updated_at"],
    )


def _result_text(decision: AgentDecision) -> str:
    return decision.reply


def _history_result_text(result: dict) -> str:
    """Read both reply records and legacy reports without rewriting stored data."""
    if "reply" in result:
        return result["reply"]
    pieces = [
        ("情境概括", result.get("summary")),
        ("主要情绪", "、".join(result.get("emotions", []))),
        ("核心矛盾", result.get("conflict")),
        ("你可能在意的", result.get("user_needs")),
        ("可以尝试", "\n".join(f"{i+1}. {v}" for i, v in enumerate(result.get("suggestions", [])))),
        ("下一步", result.get("next_step")),
        ("安全说明", result.get("safety_note")),
    ]
    return "\n\n".join(f"### {label}\n\n{value}" for label, value in pieces if value)


class GuardedTextStream:
    def __init__(self, *, guard, context: str, emit: Callable[[str], None],
                 progress: Callable[[str], None] | None = None):
        self.guard = guard
        self.context = context
        self.emit = emit
        self.progress = progress
        self.visible = ""
        self.error: BusinessError | None = None
        self.started = time.monotonic()
        self.first_emit_at: float | None = None
        self.guard_checks = 0
        self.guard_duration_ms = 0.0
        self.completion_logged = False
        self.threshold = 24
        self.flush_interval = 0.3
        self.queue: asyncio.Queue[tuple[str, bool] | None] = asyncio.Queue()
        self.finished = False
        self.worker = asyncio.create_task(self._consume())

    def _format(self, path: JSONPath, event: str, text: str) -> str:
        # The Provider still parses JSON/SSE; only the reply value is user-visible.
        return text if path == ("reply",) and event == "delta" else ""

    async def __call__(self, path: JSONPath, event: str, text: str) -> None:
        if self.finished or self.error is not None:
            return
        formatted = self._format(path, event, text)
        if formatted or event == "end":
            await self.queue.put((formatted, event == "end"))

    async def _consume(self) -> None:
        pending = ""
        while True:
            try:
                if pending:
                    item = await asyncio.wait_for(self.queue.get(), timeout=self.flush_interval)
                else:
                    item = await self.queue.get()
            except TimeoutError:
                await self._check_and_emit(pending)
                pending = ""
                continue
            if item is None:
                await self._check_and_emit(pending)
                return
            text, force = item
            pending += text
            has_boundary = any(char in pending for char in "。！？!?;；")
            if pending and (force or len(pending) >= self.threshold or has_boundary):
                await self._check_and_emit(pending)
                pending = ""

    async def _check_and_emit(self, text: str) -> None:
        if not text or self.error is not None:
            return
        if self.progress:
            self.progress("output_safety_check")
        check_started = time.monotonic()
        try:
            result = await self.guard.check_output(self.context, self.visible + text)
        except (GuardUnavailableError, GuardProtocolError) as exc:
            logger.warning("guard_failed phase=output_stream category=%s", type(exc).__name__)
            self.error = BusinessError(503, "safety_check_unavailable")
            return
        finally:
            self.guard_checks += 1
            self.guard_duration_ms += (time.monotonic() - check_started) * 1000
        if result.safety != QwenSafetyLevel.SAFE:
            self.error = BusinessError(503, "output_rejected")
            return
        self.visible += text
        if self.first_emit_at is None:
            self.first_emit_at = time.monotonic()
        self.emit(text)

    async def finish(self) -> None:
        if not self.finished:
            self.finished = True
            await self.queue.put(None)
        await self.worker
        if not self.completion_logged:
            self.completion_logged = True
            first_emit_ms = (self.first_emit_at - self.started) * 1000 if self.first_emit_at else None
            logger.info(
                "guarded_text_stream_completed duration_ms=%.1f first_emit_ms=%s "
                "guard_checks=%d guard_duration_ms=%.1f visible_chars=%d error=%s",
                (time.monotonic() - self.started) * 1000,
                f"{first_emit_ms:.1f}" if first_emit_ms is not None else "none",
                self.guard_checks,
                self.guard_duration_ms,
                len(self.visible),
                self.error.code if self.error else "none",
            )


class AnalysisService:
    def __init__(self, *, database, provider, guard, settings, graph=None):
        self.database = database
        self.provider = provider
        self.guard = guard
        self.settings = settings
        self.graph = graph or EmoraAgentGraph(EmoraAgent(provider))
        self._session_locks = {}

    @asynccontextmanager
    async def _operation(self, session_id):
        lock, users = self._session_locks.get(session_id, (asyncio.Lock(), 0))
        self._session_locks[session_id] = (lock, users+1)
        calls, validation = [], {"schema_validation": "not_run", "domain_validation": "not_run"}
        session_token = session_id_var.set(session_id)
        calls_token = provider_metrics_var.set(calls)
        validation_token = validation_var.set(validation)
        start = time.monotonic()
        outcome = {"status": "failed", "error_code": None}
        try:
            async with lock:
                yield outcome
        except asyncio.CancelledError:
            outcome.update(status="cancelled", error_code="request_cancelled")
            raise
        except (SessionConflict, IntegrityError) as exc:
            outcome["error_code"] = "session_conflict"
            raise BusinessError(409, "session_conflict") from exc
        except Exception as exc:
            outcome["error_code"] = exc.code if isinstance(exc, BusinessError) else "persistence_or_internal_error"
            raise
        finally:
            logger.info("analysis_operation %s", json.dumps({
                "request_id": request_id_var.get(), "session_id": session_id,
                "duration_ms": round((time.monotonic()-start)*1000, 1),
                **outcome, **validation, "provider_calls": len(calls),
                "provider_errors": [c["error_code"] for c in calls if c.get("error_code")],
            }))
            provider_metrics_var.reset(calls_token)
            validation_var.reset(validation_token)
            session_id_var.reset(session_token)
            users = self._session_locks[session_id][1]-1
            if users:
                self._session_locks[session_id] = (lock, users)
            else:
                del self._session_locks[session_id]

    def _history(self, session_id):
        records = self.database.session_records(session_id, limit=self.settings.context_max_turns+1,
                                                completed_only=True)
        groups = []
        for record in records:
            group = [{"role": "user", "content": record["original_input"]}]
            if record["clarification_question"]:
                group.append({"role": "assistant", "content": record["clarification_question"]})
            if record["clarification_answer"]:
                group.append({"role": "user", "content": record["clarification_answer"]})
            group.append({"role": "assistant", "content": _history_result_text(record["result"])})
            groups.append(group)
        return groups

    def _bounded_history(self, groups, current):
        current_size = len(json.dumps(current, ensure_ascii=False))
        if current_size > self.settings.context_max_chars:
            raise BusinessError(422, "context_too_long")
        chosen = []
        size = current_size
        for group in reversed(groups[-self.settings.context_max_turns:]):
            cost = sum(len(message["content"]) for message in group)
            if size + cost > self.settings.context_max_chars:
                break
            chosen.insert(0, group)
            size += cost
        logger.info("context_budget selected_turns=%d dropped_loaded_turns=%d chars=%d limit=%d",
                    len(chosen), len(groups)-len(chosen), size, self.settings.context_max_chars)
        return [message for group in chosen for message in group]

    async def _input_guard(self, context: str):
        try:
            result = await self.guard.check_input(context)
        except (GuardUnavailableError, GuardProtocolError) as exc:
            logger.warning("guard_failed phase=input category=%s", type(exc).__name__)
            raise BusinessError(503, "safety_check_unavailable") from exc
        if result.safety == QwenSafetyLevel.UNSAFE:
            return result
        return result

    async def _run(self, *, original_input: str, question=None, answer=None, round_number=0,
                   history_groups=None,
                   reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
                   progress: Callable[[str], None] | None = None,
                   answer_delta: Callable[[str], None] | None = None):
        context = original_input
        if question and answer is not None:
            context = f"Original description:\n{original_input}\n\nClarification question:\n{question}\n\nUser answer:\n{answer}"
        current = {"original_input": original_input, "clarification_question": question,
                   "clarification_answer": answer, "clarification_round": round_number}
        history = self._bounded_history(history_groups or [], current)
        if history:
            context = json.dumps({"history": history, "current": current}, ensure_ascii=False)
        if progress:
            progress("input_safety_check")
        guard = await self._input_guard(context)
        if guard.safety == QwenSafetyLevel.UNSAFE:
            return None, guard
        if progress:
            progress(f"model_reasoning_{reasoning_effort}")
        try:
            if progress:
                progress("response_generation")
            answer_stream = GuardedTextStream(
                guard=self.guard, context=context, emit=answer_delta,
                progress=progress,
            ) if answer_delta else None
            try:
                payload = await self.graph.ainvoke(
                    original_input=original_input,
                    clarification_question=question,
                    clarification_answer=answer,
                    clarification_round=round_number,
                    reasoning_effort=reasoning_effort,
                    history=history,
                    on_text=answer_stream if answer_stream else None,
                )
            finally:
                if answer_stream:
                    await answer_stream.finish()
            if answer_stream and answer_stream.error:
                raise answer_stream.error
            _validation("schema_validation", "failed")
            decision = AgentDecision.model_validate(payload)
            _validation("schema_validation", "passed")
        except BusinessError:
            raise
        except ProviderError as exc:
            raise BusinessError(503, exc.code) from exc
        except ValidationError as exc:
            logger.warning("schema_validation status=failed model_contract=AgentDecision")
            raise BusinessError(503, "schema_validation_failed") from exc
        except ValueError as exc:
            _validation("domain_validation", "failed")
            raise BusinessError(503, "domain_validation_failed") from exc
        except Exception as exc:
            logger.warning("analysis_internal_failure category=%s", type(exc).__name__)
            raise BusinessError(503, "analysis_unavailable") from exc

        if not decision.reply.strip():
            _validation("domain_validation", "failed")
            raise BusinessError(503, "invalid_reply")
        _validation("domain_validation", "passed")
        try:
            if progress:
                progress("output_safety_check")
            output = await self.guard.check_output(context, _result_text(decision))
        except (GuardUnavailableError, GuardProtocolError) as exc:
            logger.warning("guard_failed phase=output category=%s", type(exc).__name__)
            raise BusinessError(503, "safety_check_unavailable") from exc
        if output.safety != QwenSafetyLevel.SAFE:
            raise BusinessError(503, "output_rejected")
        return decision, guard

    async def create(self, text: str, *, session_id: str | None = None,
                     reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
                     progress=None, answer_delta=None):
        new_session = session_id is None
        session_id = session_id or uuid4().hex
        async with self._operation(session_id) as outcome:
            previous = self.database.session_records(session_id, limit=1)
            if not new_session and not previous:
                raise BusinessError(404, "session_not_found")
            if previous and previous[-1]["status"] == "waiting_clarification":
                # A legacy pending question can be answered/corrected through the normal chat API.
                response = await self._continue_with_answer(
                    previous[-1]["id"], text, reasoning_effort=reasoning_effort,
                    progress=progress, answer_delta=answer_delta)
                outcome.update(status=response.status.value, error_code=response.error_code,
                               analysis_id=response.id)
                return response
            response = await self._create(text, session_id=session_id,
                expected_turn=previous[-1]["turn_index"]+1 if previous else 0,
                reasoning_effort=reasoning_effort, progress=progress, answer_delta=answer_delta)
            if new_session and response.status in {AnalysisStatus.BLOCKED, AnalysisStatus.FAILED}:
                try:
                    self.database.set_session_title(session_id, "新对话")
                except Exception as exc:
                    logger.warning("session_title_persistence_failed error_type=%s",
                                   type(exc).__name__)
            outcome.update(status=response.status.value,error_code=response.error_code,analysis_id=response.id)
            return response

    async def ensure_session_title(self, session_id):
        async with self._operation(session_id) as outcome:
            session = self.database.get_session(session_id)
            if session is None:
                raise BusinessError(404, "session_not_found")
            if session.get("title"):
                return {"session_id": session_id, "title": session["title"]}
            first = self.database.first_session_record(session_id)
            title = None
            if first and first["status"] in {
                AnalysisStatus.COMPLETED.value, AnalysisStatus.WAITING_CLARIFICATION.value,
            }:
                generator = getattr(self.provider, "generate_session_title", None)
                if generator is not None:
                    source = (first["result"] or {}).get("summary") or first["original_input"]
                    try:
                        title = await generator(source)
                    except Exception as exc:
                        logger.warning("session_title_generation_failed error_type=%s",
                                       type(exc).__name__)
            title = title or "新对话"
            self.database.set_session_title(session_id, title)
            saved = self.database.get_session(session_id)
            outcome.update(status="completed")
            return {"session_id": session_id, "title": saved["title"] if saved else title}

    async def _create(self, text: str, *, session_id: str, expected_turn: int,
                     reasoning_effort: ReasoningEffort = DEFAULT_REASONING_EFFORT,
                     progress: Callable[[str], None] | None = None,
                     answer_delta: Callable[[str], None] | None = None) -> AnalysisResponse:
        validate_input_length(text, self.settings.max_input_length)
        try:
            decision, guard = await self._run(original_input=text,
                                              history_groups=self._history(session_id),
                                              reasoning_effort=reasoning_effort,
                                              progress=progress, answer_delta=answer_delta)
        except BusinessError as exc:
            logger.warning("analysis_failed code=%s", exc.code)
            if progress:
                progress("result_persistence")
            record = self.database.create(text, "failed", error_code=exc.code,
                                          session_id=session_id, expected_turn=expected_turn,
                                          reasoning_effort=reasoning_effort)
            return _response(record)
        if progress:
            progress("result_persistence")
        if decision is None:
            urgent = any(category in guard.categories for category in (
                QwenSafetyCategory.SUICIDE_SELF_HARM, QwenSafetyCategory.VIOLENT,
            ))
            record = self.database.create(text, "blocked", guard=guard,
                                          session_id=session_id, expected_turn=expected_turn,
                                          error_code="unsafe_input_seek_support" if urgent else "unsafe_input",
                                          reasoning_effort=reasoning_effort)
            return _response(record)
        record = self.database.create(text, "completed", guard=guard,
                                      session_id=session_id, expected_turn=expected_turn,
                                      result=decision.model_dump(mode="json"),
                                      reasoning_effort=reasoning_effort)
        return _response(record)

    async def continue_with_answer(self, analysis_id: str, answer: str, *,
                                   reasoning_effort: ReasoningEffort | None = None,
                                   progress=None, answer_delta=None):
        previous = self.database.get(analysis_id)
        if previous is None:
            raise BusinessError(404, "analysis_not_found")
        async with self._operation(previous["session_id"]) as outcome:
            response = await self._continue_with_answer(analysis_id,answer,
                reasoning_effort=reasoning_effort,progress=progress,answer_delta=answer_delta)
            outcome.update(status=response.status.value,error_code=response.error_code,analysis_id=response.id)
            return response

    async def _continue_with_answer(self, analysis_id: str, answer: str, *,
                                   reasoning_effort: ReasoningEffort | None = None,
                                   progress: Callable[[str], None] | None = None,
                                   answer_delta: Callable[[str], None] | None = None) -> AnalysisResponse:
        validate_input_length(answer, self.settings.max_input_length)
        previous = self.database.get(analysis_id)
        if previous is None:
            raise BusinessError(404, "analysis_not_found")
        if previous["status"] != AnalysisStatus.WAITING_CLARIFICATION.value:
            raise BusinessError(422, "analysis_not_waiting_clarification")
        reasoning_effort = previous.get("reasoning_effort", DEFAULT_REASONING_EFFORT) \
            if reasoning_effort is None else reasoning_effort
        try:
            decision, guard = await self._run(
                original_input=previous["original_input"],
                history_groups=self._history(previous["session_id"]),
                question=previous["clarification_question"], answer=answer, round_number=1,
                progress=progress,
                answer_delta=answer_delta,
                reasoning_effort=reasoning_effort,
            )
        except BusinessError as exc:
            logger.warning("analysis_failed code=%s", exc.code)
            if exc.code != "analysis_not_found" and exc.code != "analysis_not_waiting_clarification":
                if progress:
                    progress("result_persistence")
                record = self.database.update(analysis_id, status="failed", clarification_answer=answer,
                                              expected_updated_at=previous["updated_at"],
                                              error_code=exc.code,
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
                                          expected_updated_at=previous["updated_at"],
                                          clarification_answer=answer,
                                          error_code="unsafe_input_seek_support" if urgent else "unsafe_input",
                                          reasoning_effort=reasoning_effort)
        else:
            record = self.database.update(analysis_id, status="completed", guard=guard,
                                          expected_updated_at=previous["updated_at"],
                                          clarification_answer=answer,
                                          result=decision.model_dump(mode="json"),
                                          reasoning_effort=reasoning_effort)
        return _response(record)

    def get(self, analysis_id: str) -> AnalysisResponse:
        record = self.database.get(analysis_id)
        if record is None:
            raise BusinessError(404, "analysis_not_found")
        return _response(record)

    def list(self) -> list[AnalysisResponse]:
        return [_response(record) for record in self.database.list()]

    def session_history(self, session_id, *, before=None):
        records, next_before = self.database.session_page(session_id, before=before)
        if not records and not self.database.session_records(session_id, limit=1):
            raise BusinessError(404, "session_not_found")
        return {"session_id":session_id,"items":[_response(record) for record in records],"next_before":next_before}

    def sessions(self):
        items = []
        for record in self.database.list_sessions():
            result = json.loads(record["result_json"]) if record["result_json"] else None
            items.append({
                "session_id": record["session_id"],
                "title": record.get("title") or (result or {}).get("summary") or "新对话",
                "status": AnalysisStatus(record["status"]),
                "created_at": record["created_at"],
                "updated_at": record["updated_at"],
                "pinned": bool(record["pinned"]),
                "archived": bool(record["archived"]),
            })
        return items

    def update_session(self, session_id, **changes):
        if session_id in self._session_locks:
            raise BusinessError(409, "session_busy")
        if not self.database.session_records(session_id, limit=1):
            raise BusinessError(404, "session_not_found")
        result = self.database.update_session(session_id, **changes)
        if result is None:
            raise BusinessError(404, "session_not_found")
        return {"session_id": session_id, "pinned": bool(result["pinned"]),
                "archived": bool(result["archived"])}

    def delete_session(self, session_id):
        if session_id in self._session_locks:
            raise BusinessError(409, "session_busy")
        if not self.database.delete_session(session_id):
            raise BusinessError(404, "session_not_found")

    async def close(self):
        return None
