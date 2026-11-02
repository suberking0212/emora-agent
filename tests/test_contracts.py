from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from packages_py.cli.main import app
from packages_py.observability import EventWriter
from packages_py.providers import (
    ProviderError,
    _materialize_analysis,
    _ModelAnalysis,
)
from packages_py.schemas import (
    AnalysisCandidate,
    Claim,
    EvidenceRef,
    InputVersion,
    RunEvent,
    AnalysisSession,
    RunRecord,
    validate_result_scope,
    validate_result,
)


def test_unknown_fields_rejected_and_valid_citation():
    session_id, run_id, input_id = uuid4(), uuid4(), uuid4()
    ref = EvidenceRef(
        input_version_id=input_id,
        source_kind="user_input",
        span_start=0,
        span_end=2,
        quoted_text="你好",
    )
    claim = Claim(kind="event", text="问候", evidence_ids=[ref.evidence_id])
    candidate = AnalysisCandidate(
        session_id=session_id,
        run_id=run_id,
        input_version_id=input_id,
        kind="final",
        claims=[claim],
        evidence_refs=[ref],
    )
    assert validate_result(candidate, "你好").claims[0].text == "问候"
    with pytest.raises(ValidationError):
        InputVersion(session_id=session_id, modality="text", unknown="x")


def test_bad_span_and_dangling_reference_rejected():
    ids = uuid4(), uuid4(), uuid4()
    ref = EvidenceRef(
        input_version_id=ids[2],
        source_kind="user_input",
        span_start=0,
        span_end=3,
        quoted_text="错误",
    )
    candidate = AnalysisCandidate(
        session_id=ids[0],
        run_id=ids[1],
        input_version_id=ids[2],
        kind="final",
        evidence_refs=[ref],
        claims=[Claim(kind="event", text="x", evidence_ids=[uuid4()])],
    )
    with pytest.raises(ValueError, match="evidence_span_invalid"):
        validate_result(candidate, "你好")


def test_reviewed_reference_requires_server_approved_scope_and_full_text():
    session_id, run_id, input_id = uuid4(), uuid4(), uuid4()
    source_id, version_id = uuid4(), uuid4()
    ref = EvidenceRef(
        source_kind="reviewed_reference", source_id=source_id,
        source_version_id=version_id, span_start=0, span_end=2, quoted_text="依据",
    )
    candidate = AnalysisCandidate(
        session_id=session_id, run_id=run_id, input_version_id=input_id,
        kind="final", evidence_refs=[ref],
    )
    with pytest.raises(ValueError, match="evidence_scope_invalid"):
        validate_result(candidate, "用户输入", {version_id: (source_id, "依据全文")}, set())
    with pytest.raises(ValueError, match="evidence_span_invalid"):
        validate_result(candidate, "用户输入", {version_id: (source_id, "其他全文")}, {source_id})
    assert validate_result(
        candidate, "用户输入", {version_id: (source_id, "依据全文")}, {source_id}
    ).evidence_refs[0].source_version_id == version_id
    input_version = InputVersion(
        input_version_id=input_id, session_id=session_id, modality="text",
        confirmed_text="用户输入", confirmation="confirmed",
    )
    session = AnalysisSession(
        session_id=session_id, subject_id=uuid4(), allowed_source_ids=[source_id]
    )
    run = RunRecord(
        run_id=run_id, session_id=session_id, input_version_id=input_id, request_id=uuid4(),
        thread_id="synthetic-thread", status="running", model_version="m",
        prompt_version="p", graph_version="g", rules_version="r",
        schema_version="s", runtime_version="rt", python_version="py",
        langgraph_version="lg", langchain_core_version="lc",
        checkpointer_version="cp", checkpoint_compatibility_profile="pg.v1",
        dependency_lock_hash="hash", workflow_state_schema_version="state.v1",
        provider="test", config_version="cfg", credential_ref="ref",
    )
    assert validate_result_scope(
        candidate, input_version, session, run, {version_id: (source_id, "依据全文")}
    )
    with pytest.raises(ValueError, match="evidence_scope_invalid"):
        validate_result_scope(
            candidate, input_version, session, run, {version_id: (uuid4(), "依据全文")}
        )


def test_provider_materializes_ids_and_unicode_evidence_and_deduplicates_quotes():
    from packages_py.schemas import ModelCallContext

    context = ModelCallContext(
        text="他说：‘你好🌱’。", session_id=uuid4(), run_id=uuid4(),
        input_version_id=uuid4(), clarification_rounds=0, clarification_allowed=False,
    )
    output = _ModelAnalysis.model_validate({
        "kind": "final", "claims": [{
            "kind": "event", "text": "对方说你好", "evidence_quotes": ["你好🌱"],
            "counter_evidence_quotes": [],
        }],
        "suggestions": [{
            "text": "回应", "supporting_claim_indexes": [0], "evidence_quotes": ["你好🌱"],
            "applicability": "适用", "limitations": "有限",
        }],
    })
    result = _materialize_analysis(output, context)
    assert result.claims[0].claim_id
    assert result.suggestions[0].suggestion_id
    assert result.evidence_refs[0].span_start == context.text.index("你好🌱")
    assert result.evidence_refs[0].span_end - result.evidence_refs[0].span_start == len("你好🌱")
    assert result.claims[0].evidence_ids == result.suggestions[0].evidence_ids


@pytest.mark.parametrize("mutate", ["missing_quote", "duplicate_quote", "bad_claim_index", "duplicate_index"])
def test_provider_rejects_invalid_evidence_or_relations(mutate):
    from packages_py.schemas import ModelCallContext

    context = ModelCallContext(
        text="有效证据", session_id=uuid4(), run_id=uuid4(), input_version_id=uuid4(),
        clarification_rounds=0, clarification_allowed=False,
    )
    claim = {"kind": "event", "text": "事实", "evidence_quotes": ["有效证据"],
             "counter_evidence_quotes": []}
    suggestion = {"text": "建议", "supporting_claim_indexes": [0],
                  "evidence_quotes": ["有效证据"], "applicability": "适用", "limitations": "有限"}
    if mutate == "missing_quote":
        claim["evidence_quotes"] = ["不存在"]
    elif mutate == "duplicate_quote":
        claim["evidence_quotes"] = ["有效证据", "有效证据"]
    elif mutate == "bad_claim_index":
        suggestion["supporting_claim_indexes"] = [1]
    else:
        suggestion["supporting_claim_indexes"] = [0, 0]
    output = _ModelAnalysis.model_validate({"kind": "final", "claims": [claim], "suggestions": [suggestion]})
    with pytest.raises(ProviderError, match="provider_domain_invalid"):
        _materialize_analysis(output, context)


def test_event_allowlist_redaction_and_session_purge(tmp_path):
    session_id, run_id, input_id = uuid4(), uuid4(), uuid4()
    path = tmp_path / "events.jsonl"
    event = RunEvent(
        run_id=run_id,
        session_id=session_id,
        input_version_id=input_id,
        thread_id="thread-secret",
        step="analyze",
        status="succeeded",
        provider="openai",
        model_version="model",
        prompt_version="prompt",
        graph_version="graph",
        rules_version="rules",
        schema_version="schema",
        runtime_version="runtime",
        checkpoint_compatibility_profile="pg.v1",
        dependency_lock_hash="abc",
        timestamp=datetime.now(timezone.utc),
    )
    writer = EventWriter(path)
    writer.emit(event)
    line = path.read_text(encoding="utf-8")
    assert '"thread_id": "thread-secret"' in line and '"input_summary": null' in line
    assert "private-input" not in line and "api-key-value" not in line
    writer.purge_session(session_id)
    assert path.read_text(encoding="utf-8") == ""


def test_cli_rejects_conflicting_inputs_without_provider():
    runner = CliRunner()
    no_input = runner.invoke(app, ["analyze"])
    multiple_inputs = runner.invoke(app, ["analyze", "--text", "hello", "--image", "image.png"])
    conflicting_resume = runner.invoke(
        app,
        ["resume", str(uuid4()), str(uuid4()), "--text", "new text", "--skip"],
    )
    assert no_input.exit_code == 2 and '"invalid_request"' in no_input.output
    assert multiple_inputs.exit_code == 2 and '"invalid_request"' in multiple_inputs.output
    assert conflicting_resume.exit_code == 2 and '"invalid_request"' in conflicting_resume.output
