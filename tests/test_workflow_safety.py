from types import SimpleNamespace
from uuid import uuid4

import pytest

from packages_py.schemas import (
    AnalysisCandidate,
    Claim,
    ContextUnderstanding,
    EvidenceRef,
    ModelCallContext,
    SafetyDecision,
)
from packages_py.workflow import build_graph


def _workflow(*, text="讨论安排", clarification=False, output_route="normal", failure=None):
    context = ModelCallContext(
        text=text,
        session_id=uuid4(),
        run_id=uuid4(),
        input_version_id=uuid4(),
        clarification_rounds=0,
        clarification_allowed=clarification,
    )
    saved = []
    artifacts = {}
    candidate = AnalysisCandidate(
        session_id=context.session_id,
        run_id=context.run_id,
        input_version_id=context.input_version_id,
        kind="initial" if clarification else "final",
        problem_summary="需要确认讨论目标",
        clarification_question="希望达成什么？" if clarification else None,
    )
    if failure == "invalid_evidence":
        ref = EvidenceRef(
            source_kind="user_input",
            input_version_id=context.input_version_id,
            span_start=0,
            span_end=3,
            quoted_text="不存在",
        )
        candidate.evidence_refs = [ref]
        candidate.claims = [Claim(kind="event", text="事件", evidence_ids=[ref.evidence_id])]

    class Repository:
        output_max_revisions = 0 if failure in {"domain", "invalid_evidence"} else 1

        def emit_event(self, *_args, **_kwargs):
            pass

        def load_input(self, *_args):
            return SimpleNamespace(confirmed_text=context.text)

        def load_artifact(self, _state, artifact_id, kind):
            if kind == "understanding":
                return ContextUnderstanding()
            return artifacts[artifact_id]

        def store_artifact(self, _state, kind, payload):
            artifact_id = f"{kind}-{len(artifacts)}"
            artifacts[artifact_id] = payload
            return artifact_id

        def validate_understanding(self, *_args):
            pass

        def validate_candidate(self, value, validation_context):
            if failure == "domain":
                raise ValueError("evidence_span_invalid")
            if failure == "invalid_evidence":
                from packages_py.schemas import validate_result

                validate_result(value, validation_context.text)

        def save_candidate(self, value):
            saved.append(value)
            return uuid4()

        def get_candidate(self, _result_id):
            return candidate

        def finalize_candidate(self, value, _state):
            return SimpleNamespace(
                kind=value.kind,
                clarification_question=value.clarification_question,
                result_version_id=uuid4(),
            )

    class Provider:
        def understand(self, *, context):
            return ContextUnderstanding()

        def analyze(self, *, context, understanding):
            if failure == "model":
                raise RuntimeError("model_call_failed")
            return candidate

        def classify_safety(self, *, phase, candidate=None, **_kwargs):
            if phase == "output" and candidate.kind != "safety" and failure == "safety_error":
                raise RuntimeError("safety_review_unavailable")
            if phase == "output" and candidate.kind != "safety" and output_route != "normal":
                return SafetyDecision(route=output_route, risk_flags=["uncertain"])
            return SafetyDecision(route="normal")

    graph = build_graph(repository=Repository(), provider=Provider()).compile()
    state = {
        "session_id": str(context.session_id),
        "run_id": str(context.run_id),
        "input_version_id": str(context.input_version_id),
        "thread_id": str(uuid4()),
        "clarification_rounds": 0,
        "clarification_max_rounds": 2,
        "clarification_skipped": not clarification,
    }
    return graph, state, saved


def test_normal_and_clarification_candidates_reach_save_only_after_review():
    for clarification in (False, True):
        graph, state, saved = _workflow(clarification=clarification)
        result = graph.invoke(state)
        assert len(saved) == 1
        assert result["phase"] == ("waiting_clarification" if clarification else "completed")


def test_input_safety_refusal_persists_only_safety_candidate():
    graph, state, saved = _workflow(text="我担心自杀风险")
    graph.invoke(state)
    assert len(saved) == 1
    assert saved[0].kind == "safety"


def test_output_safety_refusal_routes_to_safe_candidate_before_save():
    graph, state, saved = _workflow(output_route="uncertain")
    graph.invoke(state)
    assert len(saved) == 1
    assert saved[0].kind == "safety"


def test_diagnostic_metadata_contains_only_codes_and_decisions(caplog):
    import json
    import logging

    secret_text = "诊断私密文本，不应出现在日志"
    caplog.set_level(logging.INFO, logger="packages_py.workflow_diagnostics")
    graph, state, saved = _workflow(text=secret_text, output_route="uncertain")
    result = graph.invoke(state)
    records = [r.workflow_metadata for r in caplog.records if hasattr(r, "workflow_metadata")]
    assert records
    assert secret_text not in json.dumps(records, ensure_ascii=False)
    assert all(set(r) <= {
        "node", "route", "kind", "raw_kind", "risk_flag_codes", "phase",
        "gate_decision", "validation_result",
        "call_id",
    } for r in records)
    assert any(r.get("kind") == "safety" and r.get("gate_decision") == "persisted"
               for r in records)
    assert result["phase"] == "completed"
    assert saved[0].kind == "safety"


def test_diagnostic_unknown_risk_flags_never_log_model_text():
    from packages_py.workflow import _diagnostic_flags

    assert _diagnostic_flags(["self_harm", "model supplied private text"]) == [
        "self_harm", "unknown",
    ]


@pytest.mark.parametrize("failure", ["safety_error", "model", "domain", "invalid_evidence"])
def test_gate_model_and_domain_failures_never_save_candidate(failure):
    graph, state, saved = _workflow(failure=failure)
    if failure == "safety_error":
        with pytest.raises(RuntimeError, match="safety_review_unavailable"):
            graph.invoke(state)
    elif failure == "model":
        with pytest.raises(RuntimeError, match="model_call_failed"):
            graph.invoke(state)
    else:
        result = graph.invoke(state)
        assert result["phase"] == "failed"
    assert saved == []
