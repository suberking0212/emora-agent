"""Check retry limits and total deadlines independently of provider/network behavior."""
import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from openai import APIConnectionError, BadRequestError

from packages_py.providers import OpenAICompatibleProvider, ProviderError
from packages_py.schemas import SafetyDecision


@pytest.mark.parametrize('route', ['normal', 'safety', 'uncertain', 'private-invalid-value'])
def test_safety_wire_parse_and_provider_routes_match(monkeypatch, caplog, route):
    import json
    import logging
    from uuid import uuid4

    from packages_py.schemas import ContextUnderstanding, ModelCallContext

    real_client = httpx.AsyncClient

    async def respond(request):
        body = json.loads(request.content)
        assert body['response_format']['json_schema']['strict'] is True
        assert body['response_format']['json_schema']['schema']['properties']['route']['enum'] == [
            'normal', 'safety', 'uncertain',
        ]
        assert 'uncertain when safety' in body['messages'][0]['content']
        return httpx.Response(200, headers={'x-request-id': 'request-safe'}, json={
            'id': 'response-safe', 'object': 'chat.completion', 'created': 0, 'model': 'test',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': json.dumps({
                    'route': route, 'risk_flags': ['private-risk-text'],
                }),
            }}],
        })

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    provider = OpenAICompatibleProvider('test', 'private-key', 'https://example.invalid/v1', 10, 1)
    context = ModelCallContext(text='private-input-text', session_id=uuid4(), run_id=uuid4(),
                               input_version_id=uuid4(), clarification_rounds=0,
                               clarification_allowed=False)
    with caplog.at_level(logging.INFO, logger='packages_py.providers'):
        if route == 'private-invalid-value':
            with pytest.raises(ProviderError, match='provider_schema_invalid'):
                provider.classify_safety(context=context, phase='post',
                                         understanding=ContextUnderstanding())
        else:
            result = provider.classify_safety(context=context, phase='post',
                                              understanding=ContextUnderstanding())
            assert result.route == route
            assert result._diagnostic_call_id
            assert '_diagnostic_call_id' not in result.model_dump()
    metadata = [r.safety_metadata for r in caplog.records if hasattr(r, 'safety_metadata')]
    assert metadata[0]['raw_route'] == (route if route != 'private-invalid-value' else 'invalid')
    assert metadata[0]['parse_status'] == ('passed' if route != 'private-invalid-value' else 'failed')
    assert metadata[0]['upstream_request_id'] == 'request-safe'
    if route != 'private-invalid-value':
        assert metadata[-1]['raw_route'] == metadata[-1]['parsed_route'] == metadata[-1]['provider_route']
    serialized = json.dumps(metadata)
    assert all(secret not in serialized for secret in (
        'private-key', 'private-input-text', 'private-risk-text', 'private-invalid-value',
    ))


@pytest.mark.parametrize('failure,attempts', [('connection', 2), ('bad_request', 1), ('schema', 1)])
def test_retry_budget_and_nonretryable_failures(failure, attempts):
    provider = OpenAICompatibleProvider('test', 'test-key', 'https://example.invalid/v1', 10, 2)
    client = MagicMock()
    create = AsyncMock()
    client.with_options.return_value.chat.completions.create = create
    request = httpx.Request('POST', 'https://example.invalid/v1/chat/completions')
    if failure == 'connection':
        create.side_effect = APIConnectionError(request=request)
    elif failure == 'bad_request':
        create.side_effect = BadRequestError(
            'invalid request', response=httpx.Response(400, request=request), body=None
        )
    else:
        create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"route":"invalid"}'))]
        )
    expected_code = {
        'schema': 'provider_schema_invalid',
        'connection': 'provider_transport_error',
        'bad_request': 'provider_http_error',
    }[failure]
    with pytest.raises(ProviderError, match=expected_code):
        asyncio.run(provider._structured_attempts(client, SafetyDecision, 'test', {}, {}))
    assert create.call_count == attempts


def test_total_deadline_cancels_request_even_while_it_is_active(monkeypatch):
    provider = OpenAICompatibleProvider('test', 'test-key', 'https://example.invalid/v1', .1, 2)
    cancelled = []

    async def keepalive(*args, **kwargs):
        try:
            while True:
                await asyncio.sleep(.01)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(provider, '_structured_attempts', keepalive)
    started = time.monotonic()
    with pytest.raises(ProviderError, match='provider_timeout'):
        provider._structured(SafetyDecision, 'test', {})
    assert cancelled == [True]
    assert time.monotonic() - started < 2


@pytest.mark.parametrize('outcome', ['ok', 'timeout'])
def test_http_metadata_is_available_without_recording_secrets(monkeypatch, caplog, outcome):
    import logging

    real_client = httpx.AsyncClient

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(5)
            yield b'{}'

    async def respond(request):
        if outcome == 'timeout':
            return httpx.Response(200, headers={'x-request-id': 'request-safe'}, stream=SlowBody())
        return httpx.Response(200, headers={'x-request-id': 'request-safe'}, json={
            'id': 'gen-safe', 'object': 'chat.completion', 'created': 0, 'model': 'test',
            'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {
                'role': 'assistant', 'content': '{"route":"normal","risk_flags":[]}',
            }}],
        })

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, 'AsyncClient', Client)
    provider = OpenAICompatibleProvider('test', 'secret-key-never-log',
                                       'https://example.invalid/v1', .1, 2)
    with caplog.at_level(logging.INFO, logger='packages_py.providers'):
        if outcome == 'timeout':
            with pytest.raises(ProviderError, match='provider_timeout') as caught:
                provider._structured(SafetyDecision, 'private-prompt-never-log', {})
            assert caught.value.details['upstream_request_id'] == 'request-safe'
            assert caught.value.details['attempts'] == 1
        else:
            assert provider._structured(SafetyDecision, 'private-prompt-never-log', {}).route == 'normal'
    metadata = [item.model_call for item in caplog.records if hasattr(item, 'model_call')]
    headers = next(item for item in metadata if 'headers_ms' in item)
    assert headers['http_status'] == 200
    assert headers['upstream_request_id'] == 'request-safe'
    assert headers['headers_ms'] >= 0
    if outcome == 'ok':
        assert any(item.get('generation_id') == 'gen-safe' for item in metadata)
    assert 'secret-key-never-log' not in str(metadata) + caplog.text
    assert 'private-prompt-never-log' not in str(metadata) + caplog.text
