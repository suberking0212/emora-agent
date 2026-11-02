import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings
from backend.contracts import AgentDecision, AnalysisResponse, AnalysisStatus
from backend.provider import ProviderAdapter


def _chunk(content=None, finish_reason=None):
    choice = SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice])


class _Stream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __aiter__(self):
        async def iterate():
            for chunk in self.chunks:
                yield chunk
        return iterate()

    async def close(self):
        self.closed = True


def test_provider_requests_native_stream_and_parses_complete_json(monkeypatch):
    content = json.dumps({
        "action": "ask_clarification",
        "clarification_question": "你愿意多说一点吗？",
        "result": None,
    }, ensure_ascii=False)
    stream = _Stream([_chunk(content[:20]), _chunk(content[20:]), _chunk(finish_reason="stop")])
    provider = ProviderAdapter(model="gpt-5.6-terra", api_key="unused", api_base="https://example.test/v1",
                              timeout_seconds=1)
    create = AsyncMock(return_value=stream)
    monkeypatch.setattr(provider.client.chat.completions, "create", create)
    try:
        payload = asyncio.run(provider.complete({"original_input": "你好"}, AgentDecision))
    finally:
        asyncio.run(provider.close())

    assert create.call_args.kwargs["stream"] is True
    assert payload["action"] == "ask_clarification"
    assert stream.closed


def test_stream_route_sends_only_post_service_safe_text(tmp_path):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path / 'stream.db'}"))
    response = AnalysisResponse(
        id="analysis-1", original_input="你好", status=AnalysisStatus.WAITING_CLARIFICATION,
        clarification_question="你愿意多说一点吗？", safety_level="Safe", safety_categories=[],
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    stages = []

    class Service:
        async def create(self, text, *, deep_thinking=False, progress=None):
            stages.append("output_guard_and_persistence_complete")
            return response

    with TestClient(app) as client:
        app.state.service = Service()
        result = client.post("/api/analyses/stream", json={"text": "你好"})

    assert result.status_code == 200
    assert result.headers["content-type"].startswith("text/event-stream")
    frames = [frame for frame in result.text.split("\n\n") if frame.strip()]
    events = [frame.splitlines()[0].removeprefix("event: ") for frame in frames]
    payloads = [json.loads(frame.split("data: ", 1)[1]) for frame in frames if "data: " in frame]
    deltas = [payload["text"] for event, payload in zip(events, payloads) if event == "delta"]

    assert events[0] == "status"
    assert events[-1] == "complete"
    assert "".join(deltas) == response.clarification_question
    assert stages == ["output_guard_and_persistence_complete"]
