"""Diagnostics must measure one request without leaking or accepting invalid output."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts import stream_diagnostics as stream


@pytest.mark.parametrize('outcome', ['ok', 'array', 'incomplete', 'http', 'transport', 'timeout'])
def test_stream_measurement_validation_and_retry_boundary(monkeypatch, outcome):
    rows = []
    requests = []
    provider = SimpleNamespace(
        model='fixture', timeout=1,
        _connection={'api_key': 'secret-key', 'base_url': 'https://example.invalid/v1'},
    )
    monkeypatch.setattr(stream.diagnostic, 'configured_provider', lambda: provider)
    monkeypatch.setattr(stream.diagnostic, 'record',
                        lambda event, **data: rows.append({'event': event, **data}))
    real_client = httpx.AsyncClient

    class SlowBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            await asyncio.sleep(10)
            yield b'data: [DONE]\n\n'

    async def respond(request):
        requests.append(json.loads(request.content))
        if outcome == 'http':
            return httpx.Response(503, text='private upstream response')
        if outcome == 'transport':
            raise httpx.ConnectError('private transport detail', request=request)
        if outcome == 'timeout':
            return httpx.Response(200, stream=SlowBody())
        output = [{'private': 'private output'}] if outcome == 'array' else {'kind': 'final'}
        chunks = [
            {'id': 'gen-fixture', 'choices': [{'delta': {'reasoning': 'private reasoning'}}]},
            {'choices': [{'delta': {'content': json.dumps(output)}, 'finish_reason': 'stop'}]},
        ]
        content = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks)
        if outcome != 'incomplete':
            content += 'data: [DONE]\n\n'
        return httpx.Response(200, text=content, headers={'x-request-id': 'request-fixture'})

    class Client(real_client):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(stream.httpx, 'AsyncClient', Client)
    asyncio.run(stream.run(timeout_seconds=.02, reasoning_max_tokens=1024))
    assert len(requests) == 1
    assert requests[0]['reasoning'] == {'max_tokens': 1024}
    final = rows[-1]
    assert final['event'] == 'stream_finished'
    if outcome == 'ok':
        assert final['status'] == 'ok'
        assert final['first_reasoning_ms'] <= final['last_reasoning_ms']
        assert final['last_reasoning_ms'] <= final['first_content_ms'] <= final['duration_ms']
        assert final['upstream_request_id'] == 'request-fixture'
    else:
        assert final['status'] == 'failed'
        assert final['error_category'] == {
            'array': 'provider_schema_invalid', 'incomplete': 'provider_stream_incomplete',
            'http': 'provider_http_error', 'timeout': 'provider_timeout',
            'transport': 'provider_transport_error',
        }[outcome]
        assert not any(row['event'] == 'analysis_domain_validation' for row in rows)
    if outcome == 'timeout':
        assert final['upstream_outcome'] == 'unknown'
    logged = json.dumps(rows, ensure_ascii=False)
    for private in ('secret-key', 'private reasoning', 'private output',
                    'private upstream response', 'private transport detail', stream.diagnostic.TEXT):
        assert private not in logged
