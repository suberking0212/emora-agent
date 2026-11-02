"""Natural contract integrated with real adapter/parser, graph, safety stream and SQLite.

OpenAI transport and Guard classifications are simulated; these are not quality evals.
"""
import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from backend.contracts import AgentDecision
from backend.provider import ProviderAdapter
from backend.tests.test_streaming import _Stream, _chunk
from backend.tests.test_service_reliability import LEGACY_RESULT, Guard, service
from backend.agent import EmoraAgent, EmoraAgentGraph


@pytest.mark.parametrize("payload", [{}, {"reply": ""}, {"reply": None},
    {"reply": "好的", "suggestions": []}, {"action": "complete", "result": LEGACY_RESULT}])
def test_live_contract_rejects_old_or_invalid_payloads(payload):
    with pytest.raises(ValidationError):
        AgentDecision.model_validate(payload)


def test_reply_alone_is_complete_contract():
    assert AgentDecision.model_validate({"reply": "那一刻发生了什么？"}).model_dump() == {
        "reply": "那一刻发生了什么？"}


@pytest.mark.parametrize("reject", [False, True])
def test_native_stream_through_service_guards_and_persistence(service, monkeypatch, reject):
    reply = '听起来让你失望的是承诺落空。\n\n**这一次**发生了什么？ 🌱'
    content = json.dumps({"reply": reply}, ensure_ascii=True)
    stream = _Stream([*[_chunk(content[i:i+3]) for i in range(0, len(content), 3)],
                      _chunk(finish_reason="stop")])
    provider = ProviderAdapter(model="synthetic", api_key="unused", api_base="https://example.test/v1",
                               timeout_seconds=1)
    create = AsyncMock(return_value=stream)
    monkeypatch.setattr(provider.client.chat.completions, "create", create)
    service.provider = provider
    service.graph = EmoraAgentGraph(EmoraAgent(provider))
    if reject:
        service.guard = Guard(output="Unsafe")
    deltas = []
    async def run():
        try:
            return await service.create("这是合成场景", answer_delta=deltas.append)
        finally:
            await provider.close()
    result = asyncio.run(run())
    if reject:
        assert not deltas and result.status == "failed" and result.result is None
        assert service.get(result.id).status == "failed"
    else:
        assert result.status == "completed" and result.result.reply == reply
        assert ''.join(deltas) == reply == service.get(result.id).result.reply
        assert service.guard.outputs[-1][1] == reply
        assert len(service.guard.outputs) >= 2  # streamed cumulative text and final reply
        assert all(reply.startswith(text) for _, text in service.guard.outputs)
    wire = create.call_args.kwargs
    assert wire["reasoning_effort"] == "medium" and wire["stream"] is True
    prompt = wire["messages"][0]["content"]
    assert '"reply"' in prompt and "ask_clarification" not in prompt
    assert "For complete" not in prompt and "clarification_question" not in prompt
    assert stream.closed


def test_mixed_legacy_and_natural_records_remain_readable(service):
    old = service.database.create("旧版合成困扰", "completed", result=LEGACY_RESULT)
    async def run():
        new = await service.create("纠正：并不是担心", session_id=old["session_id"])
        assert new.turn_index == 1
        assert service.get(old["id"]).result.model_dump() == LEGACY_RESULT
        assert service.database.get(old["id"])["result"] == LEGACY_RESULT
        assert "### 情境概括" in service.provider.calls[0][0]["history"][1]["content"]
        await service.create("接着说", session_id=new.session_id)
        history = service.provider.calls[-1][0]["history"]
        assert history[2]["content"] == "纠正：并不是担心"
        assert history[3]["content"] == new.result.reply
        assert len(service.session_history(new.session_id)["items"]) == 3
    asyncio.run(run())


def test_natural_title_uses_original_input_and_only_once(service):
    generate = AsyncMock(return_value="合成会话")
    service.provider.generate_session_title = generate
    async def run():
        first = await service.create("合成的开场内容")
        assert service.sessions()[0]["title"]
        assert (await service.ensure_session_title(first.session_id))["title"] == "合成会话"
        await service.ensure_session_title(first.session_id)
    asyncio.run(run())
    generate.assert_awaited_once_with("合成的开场内容")


def test_stream_does_not_release_a_later_rejected_chunk(service):
    from backend.contracts import Qwen3GuardResult
    from backend.services import GuardedTextStream
    class SelectiveGuard(Guard):
        async def check_output(self, context, text):
            return Qwen3GuardResult(phase="output", safety="Unsafe" if "拒绝片段" in text else "Safe",
                categories=["None"], refusal="No", model_id="simulated")
    async def run():
        emitted = []
        stream = GuardedTextStream(guard=SelectiveGuard(), context="合成", emit=emitted.append)
        await stream(("reply",), "delta", "已检查的片段。")
        await stream(("reply",), "delta", "拒绝片段。")
        await stream(("reply",), "end", "")
        await stream.finish()
        assert ''.join(emitted) == "已检查的片段。"
        assert stream.error.code == "output_rejected"
    asyncio.run(run())
