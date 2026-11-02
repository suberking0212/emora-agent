"""One bounded live analysis request; retain metadata, never response text."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
from pydantic import ValidationError

from packages_py.providers import (
    ANALYSIS_RULES,
    EVIDENCE_RULES,
    ProviderError,
    _evidence_payload,
    _materialize_analysis,
    _ModelAnalysis,
    _strict_json_schema,
)
from packages_py.schemas import Claim, ContextUnderstanding, EvidenceRef, validate_result
from scripts import provider_diagnostics as diagnostic


async def run(timeout_seconds=60, sample_number=1, reasoning_max_tokens=None):
    diagnostic.OUTPUT = Path(__file__).resolve().parents[1] / (
        'doc/阶段一测试指导/provider-stream-diagnostics-20261025.jsonl'
    )
    record = diagnostic.record
    provider = diagnostic.configured_provider()
    # Diagnostic observation window only; saved config remains unchanged.
    provider.timeout = timeout_seconds
    context = diagnostic.context()
    ref = EvidenceRef(source_kind='user_input', input_version_id=context.input_version_id,
                      span_start=0, span_end=len(context.text), quoted_text=context.text)
    understanding = ContextUnderstanding(claims=[Claim(
        kind='event', text='同事提议讨论计划', evidence_ids=[ref.evidence_id],
    )], evidence_refs=[ref])
    # Same production prompt/schema, fixed understanding to isolate the analysis request.
    body = {
        'model': provider.model, 'stream': True,
        'messages': [
            {'role': 'system', 'content': 'Analyze with evidence, uncertainty and practical suggestions.\n'
             + EVIDENCE_RULES + ANALYSIS_RULES},
            {'role': 'user', 'content': json.dumps({
                **_evidence_payload(context),
                'understanding': understanding.model_dump(mode='json'),
            }, ensure_ascii=False)},
        ],
        'response_format': {'type': 'json_schema', 'json_schema': {
            'name': _ModelAnalysis.__name__, 'strict': True,
            'schema': _strict_json_schema(_ModelAnalysis.model_json_schema()),
        }},
    }
    if reasoning_max_tokens is not None:
        body['reasoning'] = {'max_tokens': reasoning_max_tokens}
    started = time.monotonic()
    metadata = {'chunks': 0, 'content_chars': 0, 'reasoning_chars': 0,
                'generation_id': None, 'finish_reason': None,
                'first_reasoning_ms': None, 'last_reasoning_ms': None,
                'first_content_ms': None}
    content = []
    done = False

    def elapsed():
        return int((time.monotonic() - started) * 1000)

    async def request():
        nonlocal done
        async with (
            httpx.AsyncClient(trust_env=False, timeout=provider.timeout) as client,
            client.stream(
                'POST', provider._connection['base_url'].rstrip('/') + '/chat/completions',
                headers={'Authorization': 'Bearer ' + provider._connection['api_key']}, json=body,
            ) as response,
        ):
            metadata.update(http_status=response.status_code,
                            generation_id=response.headers.get('x-generation-id'),
                            upstream_request_id=response.headers.get('x-request-id'))
            record('stream_headers', duration_ms=elapsed(), **metadata)
            response.raise_for_status()
            data_lines = []
            async for line in response.aiter_lines():
                if line.startswith('data:'):
                    data_lines.append(line[5:].lstrip())
                elif not line and data_lines:
                    data = '\n'.join(data_lines)
                    data_lines.clear()
                    if data == '[DONE]':
                        done = True
                        break
                    chunk = json.loads(data)
                    metadata['chunks'] += 1
                    metadata['generation_id'] = chunk.get('id') or metadata['generation_id']
                    if metadata['chunks'] == 1:
                        record('first_chunk', duration_ms=elapsed(), **metadata)
                    if chunk.get('error'):
                        error = chunk['error']
                        record('upstream_error', code=error.get('code'),
                               error_type=(error.get('metadata') or {}).get('error_type'),
                               duration_ms=elapsed(), **metadata)
                        raise ProviderError('provider_stream_error')
                    for choice in chunk.get('choices', []):
                        delta = choice.get('delta') or {}
                        if (delta.get('reasoning') or '') and metadata['reasoning_chars'] == 0:
                            metadata['first_reasoning_ms'] = elapsed()
                            record('first_reasoning', duration_ms=elapsed())
                        if delta.get('reasoning'):
                            metadata['last_reasoning_ms'] = elapsed()
                        text = delta.get('content') or ''
                        if text and not content:
                            metadata['first_content_ms'] = elapsed()
                            record('first_content', duration_ms=elapsed())
                        if text:
                            content.append(text)
                        metadata['content_chars'] += len(text)
                        metadata['reasoning_chars'] += len(delta.get('reasoning') or '')
                        metadata['finish_reason'] = choice.get('finish_reason') or metadata['finish_reason']
                    if chunk.get('usage'):
                        usage = chunk['usage']
                        record('stream_usage', prompt_tokens=usage.get('prompt_tokens'),
                               completion_tokens=usage.get('completion_tokens'),
                               reasoning_tokens=(usage.get('completion_tokens_details') or {}).get('reasoning_tokens'))
                    if metadata['chunks'] % 100 == 0:
                        record('stream_progress', duration_ms=elapsed(), **metadata)
        if not done or metadata['finish_reason'] != 'stop':
            raise ProviderError('provider_stream_incomplete')
        decoded = json.loads(''.join(content))
        record(
            'response_shape',
            top_level_type=type(decoded).__name__,
            key_count=len(decoded) if isinstance(decoded, dict) else None,
        )
        output = _ModelAnalysis.model_validate(decoded)
        record('schema_validation', status='ok')
        result = _materialize_analysis(output, context)
        validate_result(result, context.text)
        record('analysis_domain_validation', status='ok', claims=len(result.claims),
               suggestions=len(result.suggestions), evidence_refs=len(result.evidence_refs))

    record('stream_started', sample=sample_number, model=provider.model,
           timeout_seconds=provider.timeout, max_attempts=1,
           reasoning_max_tokens=reasoning_max_tokens,
           analysis_input='deterministic_fixture_understanding')
    try:
        await asyncio.wait_for(request(), timeout=provider.timeout)
    except Exception as exc:
        category = getattr(exc, 'code', type(exc).__name__)
        if isinstance(exc, (asyncio.TimeoutError, httpx.TimeoutException)):
            category = 'provider_timeout'
            metadata['upstream_outcome'] = 'unknown'
        elif isinstance(exc, httpx.HTTPStatusError):
            category = 'provider_http_error'
        elif isinstance(exc, httpx.TransportError):
            category = 'provider_transport_error'
        details = {}
        if isinstance(exc, ValidationError):
            category = 'provider_schema_invalid'
            details['schema_errors'] = [{'path': list(e['loc']), 'type': e['type']}
                                       for e in exc.errors(include_input=False, include_url=False)]
        elif isinstance(exc, json.JSONDecodeError):
            category = 'provider_response_invalid'
        elif isinstance(exc, ValueError):
            category = 'domain_validation_failed'
        record('stream_finished', status='failed', error_category=category,
               duration_ms=elapsed(), done=done, **metadata, **details)
    else:
        record('stream_finished', status='ok', duration_ms=elapsed(), done=done, **metadata)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, choices=(60, 90, 120), default=90)
    parser.add_argument('--samples', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--reasoning-max-tokens', type=int, choices=(1024, 2048))
    args = parser.parse_args()

    async def run_samples():
        for sample_number in range(1, args.samples + 1):
            await run(args.timeout, sample_number, args.reasoning_max_tokens)

    asyncio.run(run_samples())
