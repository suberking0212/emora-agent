"""Real Service/graph/SQLite with simulated Provider and Guard."""
import asyncio
from copy import deepcopy

import pytest

from backend.config import Settings
from backend.contracts import Qwen3GuardResult
from backend.provider import ProviderError
from backend.safety import GuardProtocolError, GuardUnavailableError
from backend.services import AnalysisService
from backend.storage import Database

DECISION = {"action": "complete", "clarification_question": None, "result": {
    "summary": "概括", "emotions": ["担心"], "conflict": "矛盾", "user_needs": "需要",
    "suggestions": ["记录事实"], "next_step": "记下一件事", "safety_note": None}}


class Provider:
    model = "simulated"
    def __init__(self, outputs=None):
        self.outputs = outputs or [deepcopy(DECISION)]
        self.calls = []

    async def complete(self, context, response_model, **kwargs):
        self.calls.append((deepcopy(context), kwargs))
        item = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
        if isinstance(item, Exception):
            raise item
        return deepcopy(item)


class Guard:
    def __init__(self, level="Safe", categories=None, error=None, output="Safe"):
        self.level, self.categories, self.error, self.output = level, categories or ["None"], error, output
        self.inputs, self.outputs = [], []

    async def check_input(self, text):
        self.inputs.append(text)
        if self.error:
            raise self.error
        return Qwen3GuardResult(phase="input", safety=self.level, categories=self.categories, model_id="simulated")

    async def check_output(self, context, text):
        self.outputs.append((context,text))
        return Qwen3GuardResult(phase="output", safety=self.output, categories=["None"],
                               refusal="No", model_id="simulated")


@pytest.fixture
def service(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'test.db'}")
    database.init_db()
    service = AnalysisService(database=database, provider=Provider(),guard=Guard(),
                              settings=Settings(_env_file=None))
    yield service
    database.close()


def test_urgent_block_persists_and_never_calls_provider(service):
    service.guard = Guard(level="Unsafe", categories=["Suicide & Self-Harm"])
    response = asyncio.run(service.create("synthetic unsafe"))
    assert response.status == "blocked" and not service.provider.calls
    assert response.error_code == service.get(response.id).error_code == "unsafe_input_seek_support"


@pytest.mark.parametrize("error", [GuardProtocolError("bad"),GuardUnavailableError("offline")])
def test_guard_failure_is_failed_not_risk(service,error):
    service.guard = Guard(error=error)
    response = asyncio.run(service.create("synthetic safe"))
    assert response.status == "failed" and response.safety_level is None
    assert response.error_code == "safety_check_unavailable" and not service.provider.calls


@pytest.mark.parametrize("output", [ProviderError("provider_model_refusal"),
    ProviderError("provider_json_parse_error"), {"action":"complete","result":{}},
    {**DECISION,"result":{**DECISION["result"],"summary":" "}}])
def test_generation_failures_never_persist_success(service,output):
    service.provider.outputs = [output]
    response = asyncio.run(service.create("synthetic input"))
    assert response.status == service.get(response.id).status == "failed"
    assert response.result is None


def test_output_guard_rejects_before_completion(service):
    service.guard = Guard(output="Controversial")
    response = asyncio.run(service.create("synthetic safe"))
    assert response.status == "failed" and response.error_code == "output_rejected"
    assert response.result is None


@pytest.mark.parametrize("error", [GuardProtocolError("private raw"), GuardUnavailableError("private raw")])
def test_output_guard_fault_never_becomes_risk_or_success(service,error,monkeypatch):
    async def fail(*args):
        raise error
    monkeypatch.setattr(service.guard,"check_output",fail)
    result = asyncio.run(service.create("合成输入"))
    assert result.status == "failed" and result.error_code == "safety_check_unavailable"
    assert result.result is None and result.safety_level is None
    assert len(service.provider.calls) == 1


def test_telemetry_contains_validation_outcome_without_raw_text(service,caplog):
    service.provider.outputs = [{**DECISION,"result":{**DECISION["result"],"summary":" "}}]
    with caplog.at_level("INFO"):
        result = asyncio.run(service.create("SYNTHETIC_PRIVATE_STORY"))
    import json
    event = next(json.loads(r.getMessage().split(" ",1)[1]) for r in caplog.records
                 if r.getMessage().startswith("analysis_operation "))
    assert event["status"] == "failed" and event["domain_validation"] == "failed"
    assert event["schema_validation"] == "passed" and event["session_id"] == result.session_id
    assert "SYNTHETIC_PRIVATE_STORY" not in caplog.text


def test_cancelled_operation_releases_lock_without_completed_record(service):
    async def wait(*args,**kwargs):
        await asyncio.Event().wait()
    service.provider.complete = wait
    async def run():
        task = asyncio.create_task(service.create("synthetic"))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not service._session_locks and not service.database.list()
    asyncio.run(run())


