"""统一的 LangGraph 工作流；检查点保存标识、版本和流程状态，正文另行存储。"""

from __future__ import annotations

import logging
import time
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from packages_py.safety import classify_text, safe_message
from packages_py.schemas import AnalysisCandidate, ModelCallContext

diagnostic_logger = logging.getLogger("packages_py.workflow_diagnostics")


def _diagnostic_flags(values):
    allowed = {"self_harm", "violence", "harmful_request", "uncertain"}
    return [value if value in allowed else "unknown" for value in values or []]


class WorkflowState(TypedDict, total=False):
    session_id: str
    run_id: str
    input_version_id: str
    thread_id: str
    clarification_rounds: int
    clarification_max_rounds: int
    clarification_skipped: bool
    candidate_result_id: str
    route: Literal["normal", "safety", "uncertain"]
    error_code: str
    workflow_state_schema_version: str
    graph_version: str
    runtime_version: str
    phase: str
    understanding_artifact_id: str
    candidate_artifact_id: str
    published_result_id: str
    revision_count: int
    risk_flags: list[str]


def build_graph(*, repository, provider, event_writer=None):
    """构建包含安全门控的分析工作流，由仓储层检查所属用户和运行范围。

    检查点保存标识与阶段状态；模型产物另行存储，通过标识按需加载，
    避免将用户正文和完整模型输出直接放入工作流状态。
    """

    def instrument(name, node):
        def invoke(state):
            repository.emit_event(state, name, "started")
            started = time.monotonic()
            try:
                result = node(state)
            except Exception as exc:
                diagnostic_logger.info(
                    "workflow_node_metadata", extra={"workflow_metadata": {
                        "node": name, "validation_result": "failed",
                        "gate_decision": "abort",
                    }},
                )
                repository.emit_event(
                    state,
                    name,
                    "failed",
                    int((time.monotonic() - started) * 1000),
                    getattr(exc, "code", "output_validation_failed"),
                )
                raise
            # 诊断日志仅记录元数据，不写入节点载荷、用户正文或模型输出正文。
            diagnostic_logger.info(
                "workflow_node_metadata",
                extra={"workflow_metadata": {
                    "node": name,
                    "route": result.get("route", state.get("route")),
                    "kind": None,
                    "risk_flag_codes": _diagnostic_flags(
                        result.get("risk_flags", state.get("risk_flags"))
                    ),
                    "phase": result.get("phase"),
                    "gate_decision": (
                        "allow" if result.get("route") == "normal" else
                        "safe_response" if result.get("route") in {"safety", "uncertain"} else
                        "not_applicable"
                    ),
                    "validation_result": (
                        "failed" if result.get("error_code") else
                        "passed" if name == "understand_context" else
                        "completed" if name in {"persist_result", "finalize"} else
                        "reviewed" if name == "quality_review"
                        else "not_applicable"
                    ),
                }},
            )
            repository.emit_event(
                state,
                name,
                "waiting" if result.get("phase") == "waiting_clarification" else "succeeded",
                int((time.monotonic() - started) * 1000),
            )
            return result

        return invoke

    def context(state):
        input_version = repository.load_input(state["session_id"], state["input_version_id"])
        return ModelCallContext(
            text=input_version.confirmed_text or "",
            session_id=state["session_id"],
            run_id=state["run_id"],
            input_version_id=state["input_version_id"],
            clarification_rounds=state["clarification_rounds"],
            clarification_allowed=(
                not state["clarification_skipped"]
                and state["clarification_rounds"] < state["clarification_max_rounds"]
            ),
        )

    def pre_safety_gate(state):
        ctx = context(state)
        decision = classify_text(ctx.text)
        diagnostic_logger.info(
            "input_rule_metadata", extra={"workflow_metadata": {
                "node": "input_rule", "route": decision.route,
                "risk_flag_codes": _diagnostic_flags(decision.risk_flags),
            }},
        )
        # 固定规则命中明确风险后直接进入安全回复，避免继续调用模型。
        if decision.route == "normal":
            decision = provider.classify_safety(context=ctx, phase="pre")
            diagnostic_logger.info(
                "workflow_safety_consumed_metadata",
                extra={"workflow_metadata": {
                    "node": "pre_safety_gate", "phase": "pre",
                    "call_id": decision._diagnostic_call_id,
                    "route": decision.route,
                    "gate_decision": "allow" if decision.route == "normal" else "safe_response",
                }},
            )
        return {
            "route": decision.route,
            "risk_flags": decision.risk_flags,
            "phase": "pre_safety_gate",
        }

    def understand_context(state):
        ctx = context(state)
        understood = provider.understand(context=ctx)
        repository.validate_understanding(understood, ctx)
        artifact_id = repository.store_artifact(
            state, "understanding", understood.model_dump(mode="json")
        )
        return {"understanding_artifact_id": artifact_id, "phase": "understand_context"}

    def safety_gate(state):
        ctx = context(state)
        understanding = repository.load_artifact(
            state, state["understanding_artifact_id"], "understanding"
        )
        decision = provider.classify_safety(context=ctx, phase="post", understanding=understanding)
        diagnostic_logger.info(
            "workflow_safety_consumed_metadata",
            extra={"workflow_metadata": {
                "node": "safety_gate", "phase": "post", "route": decision.route,
                "call_id": decision._diagnostic_call_id,
                "gate_decision": "allow" if decision.route == "normal" else "safe_response",
            }},
        )
        return {"route": decision.route, "risk_flags": decision.risk_flags, "phase": "safety_gate"}

    def analyze_and_advise(state):
        ctx = context(state)
        understanding = repository.load_artifact(
            state, state["understanding_artifact_id"], "understanding"
        )
        candidate = provider.analyze(context=ctx, understanding=understanding)
        raw_kind = candidate.kind
        # 是否允许本轮追问由服务端根据澄清次数和跳过状态决定。
        candidate = candidate.model_copy(
            update={
                "kind": "initial" if ctx.clarification_allowed else "final",
                "clarification_question": candidate.clarification_question
                if ctx.clarification_allowed
                else None,
            }
        )
        artifact_id = repository.store_artifact(
            state, "candidate", candidate.model_dump(mode="json")
        )
        diagnostic_logger.info(
            "candidate_kind_metadata", extra={"workflow_metadata": {
                "node": "analyze_and_advise", "raw_kind": raw_kind,
                "kind": candidate.kind,
                "risk_flag_codes": _diagnostic_flags(candidate.risk_flags),
            }},
        )
        return {"candidate_artifact_id": artifact_id, "phase": "analyze_and_advise"}

    def clarify_if_needed(state):
        return {"phase": "clarify_if_needed"}

    def safe_response(state):
        from packages_py.schemas import AnalysisCandidate

        ctx = context(state)
        candidate = AnalysisCandidate(
            session_id=ctx.session_id,
            run_id=ctx.run_id,
            input_version_id=ctx.input_version_id,
            kind="safety",
            problem_summary="需要优先处理当前安全风险。",
            safety_message=safe_message(state.get("risk_flags", [])),
            risk_flags=state.get("risk_flags", ["uncertain"]),
        )
        artifact_id = repository.store_artifact(
            state, "candidate", candidate.model_dump(mode="json")
        )
        return {"candidate_artifact_id": artifact_id, "phase": "safe_response"}

    def quality_review(state):
        candidate = AnalysisCandidate.model_validate(
            repository.load_artifact(state, state["candidate_artifact_id"], "candidate")
        )
        try:
            repository.validate_candidate(candidate, context(state))
            diagnostic_logger.info(
                "candidate_validation_metadata", extra={"workflow_metadata": {
                    "node": "quality_review", "kind": candidate.kind,
                    "validation_result": "passed",
                }},
            )
            safety = classify_text(context(state).text)
            if candidate.kind != "safety" and safety.route == "safety":
                raise ValueError("output_safety_invalid")
        except Exception as exc:
            diagnostic_logger.info(
                "candidate_validation_metadata", extra={"workflow_metadata": {
                    "node": "quality_review", "kind": candidate.kind,
                    "validation_result": "failed",
                }},
            )
            # 限制修订次数，避免因输出结构持续不合法而无限调用模型。
            if state.get("revision_count", 0) >= repository.output_max_revisions:
                return {"error_code": "output_validation_failed", "phase": "quality_review"}
            revised = provider.revise(
                context=context(state),
                understanding=repository.load_artifact(
                    state, state["understanding_artifact_id"], "understanding"
                )
                if state.get("understanding_artifact_id")
                else None,
                candidate=candidate,
                validation_errors=[str(exc)],
            ).model_dump(mode="json")
            revised_candidate = AnalysisCandidate.model_validate(revised)
            repository.validate_candidate(revised_candidate, context(state))
            diagnostic_logger.info(
                "candidate_validation_metadata", extra={"workflow_metadata": {
                    "node": "revision_review", "kind": revised_candidate.kind,
                    "validation_result": "passed",
                }},
            )
            if (
                provider.classify_safety(
                    context=context(state), phase="output", candidate=revised_candidate
                ).route
                != "normal"
                and revised_candidate.kind != "safety"
            ):
                return {
                    "route": "uncertain",
                    "risk_flags": ["uncertain"],
                    "phase": "output_safety_rejected",
                }
            artifact_id = repository.store_artifact(state, "candidate", revised)
            return {
                "candidate_artifact_id": artifact_id,
                "revision_count": state.get("revision_count", 0) + 1,
                "phase": "quality_review",
            }
        output_safety = provider.classify_safety(
            context=context(state), phase="output", candidate=candidate
        )
        if output_safety.route != "normal" and candidate.kind != "safety":
            return {
                "route": output_safety.route,
                "risk_flags": output_safety.risk_flags,
                "phase": "output_safety_rejected",
            }
        return {"phase": "quality_review"}

    def persist_result(state):
        candidate = AnalysisCandidate.model_validate(
            repository.load_artifact(state, state["candidate_artifact_id"], "candidate")
        )
        result_id = repository.save_candidate(candidate)
        diagnostic_logger.info(
            "persistence_metadata", extra={"workflow_metadata": {
                "node": "persist_result", "kind": candidate.kind,
                "gate_decision": "persisted",
            }},
        )
        return {"candidate_result_id": str(result_id), "phase": "persist_result"}

    def finalize(state):
        candidate = repository.get_candidate(state["candidate_result_id"])
        result = repository.finalize_candidate(candidate, state)
        if result.kind == "initial" and result.clarification_question:
            return {
                "phase": "waiting_clarification",
                "published_result_id": str(result.result_version_id),
            }
        return {"phase": "completed", "published_result_id": str(result.result_version_id)}

    def clarification_interrupt(state):
        answer = interrupt(
            {
                "session_id": state["session_id"],
                "run_id": state["run_id"],
                "result_version_id": state.get("published_result_id"),
            }
        )
        if not isinstance(answer, dict):
            raise ValueError("invalid_request")
        # 恢复载荷是服务端校验过的命令，不采用模型提供的身份标识。
        return Command(
            goto="pre_safety_gate",
            update={
                **answer,
                "understanding_artifact_id": "",
                "candidate_artifact_id": "",
                "candidate_result_id": "",
                "phase": "resuming",
            },
        )

    def route_pre(state):
        return "safe_response" if state.get("route") != "normal" else "understand_context"

    def route_post(state):
        return "safe_response" if state.get("route") != "normal" else "analyze_and_advise"

    def route_quality(state):
        # 输出安全检查未通过时改走安全回复；校验修订耗尽时终止发布。
        if state.get("phase") == "output_safety_rejected":
            return "safe_response"
        return "failed" if state.get("error_code") else "persist_result"

    def route_final(state):
        return "clarification_interrupt" if state.get("phase") == "waiting_clarification" else END

    graph = StateGraph(WorkflowState)
    for name, node in (
        ("pre_safety_gate", pre_safety_gate),
        ("understand_context", understand_context),
        ("safety_gate", safety_gate),
        ("analyze_and_advise", analyze_and_advise),
        ("clarify_if_needed", clarify_if_needed),
        ("safe_response", safe_response),
        ("quality_review", quality_review),
        ("persist_result", persist_result),
        ("finalize", finalize),
        ("clarification_interrupt", clarification_interrupt),
    ):
        graph.add_node(name, node if name == "clarification_interrupt" else instrument(name, node))
    graph.add_node("failed", lambda state: {"phase": "failed"})
    graph.add_edge(START, "pre_safety_gate")
    graph.add_conditional_edges("pre_safety_gate", route_pre)
    graph.add_edge("understand_context", "safety_gate")
    graph.add_conditional_edges("safety_gate", route_post)
    graph.add_edge("analyze_and_advise", "clarify_if_needed")
    graph.add_edge("clarify_if_needed", "quality_review")
    graph.add_edge("safe_response", "quality_review")
    graph.add_conditional_edges("quality_review", route_quality)
    graph.add_edge("failed", END)
    graph.add_edge("persist_result", "finalize")
    graph.add_conditional_edges("finalize", route_final)
    graph.add_edge("clarification_interrupt", END)
    return graph
