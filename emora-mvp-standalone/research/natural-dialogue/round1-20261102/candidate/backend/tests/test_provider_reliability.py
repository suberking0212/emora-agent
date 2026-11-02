"""Simulated upstream transport, not real model capability tests."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
from openai import APIConnectionError, APITimeoutError, BadRequestError
import pytest

from backend.contracts import AgentDecision
from backend.provider import ProviderAdapter, ProviderError, provider_metrics_var, strict_json_schema


def chunk(text=None, refusal=None, finish=None, usage=None):
    return NS(choices=[] if usage else [NS(delta=NS(content=text, refusal=refusal), finish_reason=finish)],
              usage=usage, id="synthetic-completion", model="synthetic-model")


class Stream:
    def __init__(self, chunks):
        self.chunks, self.closed = chunks, False

    def __aiter__(self):
        async def iterate():
            for item in self.chunks:
                yield item
        return iterate()

    async def close(self):
        self.closed = True


def call(monkeypatch, chunks, *, mode="json_object", error=None, callback=None):
    async def run():
        provider = ProviderAdapter(model="synthetic", api_key="unused", api_base="https://example.test/v1",
                                   timeout_seconds=1, response_format=mode)
        stream = Stream(chunks)
        create = AsyncMock(side_effect=error) if error else AsyncMock(return_value=stream)
        monkeypatch.setattr(provider.client.chat.completions, "create", create)
        metrics = []
        token = provider_metrics_var.set(metrics)
        try:
            result = await provider.complete({"original_input": "current", "history": [
                {"role": "user", "content": "prior"}, {"role": "assistant", "content": "reply"}]},
                AgentDecision, reasoning_effort="low", on_text=callback)
            return result, create.call_args.kwargs, metrics, stream
        finally:
            provider_metrics_var.reset(token)
            await provider.close()
    return asyncio.run(run())


@pytest.mark.parametrize("mode", ["json_object", "json_schema"])
def test_request_contract_and_real_roles(monkeypatch, mode):
    content = '{"reply":"question"}'
    result, request, metrics, stream = call(monkeypatch, [chunk(content), chunk(finish="stop")], mode=mode)
    assert request["response_format"]["type"] == mode
    assert request["reasoning_effort"] == "low"
    assert [m["role"] for m in request["messages"]] == ["system", "user", "assistant", "user"]
    assert "history" not in json.loads(request["messages"][-1]["content"])
    assert result["reply"] == "question" and stream.closed
    assert metrics[0]["status"] == "parsed"  # not application success


def test_strict_wire_schema_preserves_domain_schema():
    original = AgentDecision.model_json_schema()
    strict = strict_json_schema(AgentDecision)
    assert set(strict["required"]) == set(strict["properties"])
    assert strict["additionalProperties"] is False
    assert strict["properties"]["reply"]["type"] == "string"
    assert AgentDecision.model_json_schema() == original


@pytest.mark.parametrize("text,code", [
    ('prefix {"action":"complete"}', "provider_json_parse_error"),
    ('```json\n{}\n```', "provider_json_parse_error"),
    ('{"a":1,"a":2}', "provider_json_parse_error"),
    ('{"a":NaN}', "provider_json_parse_error"),
    ('{"a":', "provider_json_parse_error"),
    ('[]', "provider_invalid_response_shape"),
    ('', "provider_empty_response"),
])
def test_strict_parse_rejects_invalid_output(monkeypatch, text, code):
    with pytest.raises(ProviderError, match=code):
        call(monkeypatch, [chunk(text), chunk(finish="stop")])


@pytest.mark.parametrize("refusal,finish,code", [
    ("refused", "stop", "provider_model_refusal"),
    (None, "content_filter", "provider_model_refusal"),
    (None, "length", "provider_incomplete_response"),
    (None, None, "provider_incomplete_response"),
])
def test_refusal_and_incomplete_are_not_parse_success(monkeypatch, refusal, finish, code):
    with pytest.raises(ProviderError, match=code):
        call(monkeypatch, [chunk("{}", refusal=refusal, finish=finish)])


@pytest.mark.parametrize("kind,code", [("timeout", "provider_timeout"),
    ("transport", "provider_transport_error"), ("http", "provider_http_error")])
def test_provider_error_classification(monkeypatch, kind, code):
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    errors = {"timeout": APITimeoutError(request=request),
              "transport": APIConnectionError(request=request),
              "http": BadRequestError("private upstream text", response=httpx.Response(400,request=request), body=None)}
    with pytest.raises(ProviderError, match=code):
        call(monkeypatch, [], error=errors[kind])


def test_usage_only_chunk_is_captured(monkeypatch):
    usage = NS(model_dump=lambda: {"prompt_tokens": 10,"completion_tokens": 5,"total_tokens": 15})
    _, _, metrics, _ = call(monkeypatch, [chunk("{}",finish="stop"),chunk(usage=usage)])
    assert metrics[0]["usage"]["total_tokens"] == 15


def test_missing_usage_is_unknown_and_metadata_drops_untrusted_fields(monkeypatch,caplog):
    _, _, metrics, _ = call(monkeypatch,[chunk('{}',finish="stop")])
    assert metrics[0]["usage"] is None
    usage = NS(model_dump=lambda: {"total_tokens": 5, "story":"SYNTHETIC_PRIVATE",
        "prompt_tokens":"SYNTHETIC_PRIVATE", "completion_tokens_details": {
            "reasoning_tokens": 2, "private":"SYNTHETIC_PRIVATE"}})
    with caplog.at_level("INFO"):
        _, _, metrics, _ = call(monkeypatch,[chunk('{}',finish="stop"),chunk(usage=usage)])
    assert metrics[0]["usage"] == {"total_tokens":5,"completion_tokens_details":{"reasoning_tokens":2}}
    assert "SYNTHETIC_PRIVATE" not in caplog.text


def test_prefix_cannot_be_extracted_as_streamed_json(monkeypatch):
    callback = AsyncMock()
    with pytest.raises(ProviderError, match="provider_json_parse_error"):
        call(monkeypatch,[chunk('prefix {"result":{"summary":"bad"}}',finish="stop")],callback=callback)
    callback.assert_not_called()


@pytest.mark.parametrize("refusal,finish", [("explicit refusal","stop"),(None,"content_filter")])
def test_refusal_metadata_takes_priority_over_prose_parse_failure(monkeypatch,refusal,finish):
    callback = AsyncMock()
    with pytest.raises(ProviderError,match="provider_model_refusal"):
        call(monkeypatch,[chunk("I cannot answer"),chunk(refusal=refusal,finish=finish)],callback=callback)
    callback.assert_not_called()
