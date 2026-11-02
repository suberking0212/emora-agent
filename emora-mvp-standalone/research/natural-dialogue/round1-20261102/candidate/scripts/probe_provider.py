"""Explicit real-network probes with synthetic inputs and redacted evidence only."""
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openai import AsyncOpenAI
from backend.config import Settings
from backend.contracts import AgentDecision
from backend.agent import EmoraAgent
from backend.provider import ProviderAdapter, provider_metrics_var, strict_json_schema

OUT = Path(__file__).resolve().parents[1] / "research/engineering-remediation-round1"


async def main():
    settings = Settings()
    client = AsyncOpenAI(base_url=settings.api_base, api_key=settings.api_key.get_secret_value(),
                         timeout=settings.request_timeout_seconds, max_retries=0)
    simple = {"type": "object", "properties": {"value": {"type": "string", "enum": ["verified"]}},
              "required": ["value"], "additionalProperties": False}
    cases = [("strict_contradiction", simple, "Write only the prose HELLO, never JSON.", "medium"),
             ("production_schema", strict_json_schema(AgentDecision),
              'Return JSON with reply "请补充".', "medium"),
             ("invalid_schema", {"type": "object", "properties": {"value": {"type": "impossible"}}},
              "Return JSON.", "medium")]
    for effort in ("none", "low", "medium", "high", "xhigh", "max", "invalid"):
        cases.append((f"effort_{effort}", simple, 'Return JSON with value "verified".', effort))
    rows = []
    try:
        for name, schema, prompt, effort in cases:
            started = time.monotonic()
            row = {"case": name, "requested_model": settings.model, "reasoning_effort": effort}
            try:
                stream = await client.chat.completions.create(
                    model=settings.model, messages=[{"role": "user", "content": prompt}],
                    stream=True, stream_options={"include_usage": True}, reasoning_effort=effort,
                    response_format={"type": "json_schema", "json_schema": {
                        "name": "probe", "strict": True, "schema": schema}})
                parts, refusal = [], False
                first = None
                try:
                    async for chunk in stream:
                        row["returned_model"] = chunk.model
                        row["completion_id"] = chunk.id
                        if chunk.usage:
                            row["usage"] = chunk.usage.model_dump()
                        for choice in chunk.choices:
                            refusal |= bool(choice.delta.refusal)
                            if choice.delta.content:
                                first = first or time.monotonic()
                                parts.append(choice.delta.content)
                            if choice.finish_reason:
                                row["finish_reason"] = choice.finish_reason
                finally:
                    await stream.close()
                row.update(accepted=True, refusal=refusal,
                           first_content_ms=round((first-started)*1000, 1) if first else None)
                row["synthetic_output"] = "".join(parts)
                try:
                    value = json.loads("".join(parts))
                    row["json_valid"] = True
                    row["schema_valid"] = (value == {"value": "verified"}) if schema == simple else None
                    if name == "production_schema":
                        AgentDecision.model_validate(value)
                        row["schema_valid"] = True
                except (ValueError, TypeError):
                    row["json_or_schema_invalid"] = True
            except Exception as exc:
                row.update(accepted=False, error_type=type(exc).__name__,
                           http_status=getattr(exc, "status_code", None))
            row["duration_ms"] = round((time.monotonic()-started)*1000, 1)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / "provider-probes.json").write_text(json.dumps({
                "at": datetime.now(timezone.utc).isoformat(), "tier": "real_provider_direct",
                "provider_host": client.base_url.host, "cases": rows,
            }, ensure_ascii=False, indent=2) + "\n")
    finally:
        await client.close()


async def alternatives():
    settings = Settings()
    client = AsyncOpenAI(base_url=settings.api_base, api_key=settings.api_key.get_secret_value(),
                         timeout=settings.request_timeout_seconds, max_retries=0)
    rows = []
    schema = {"type": "object", "properties": {"value": {"type": "string", "enum": ["verified"]}},
              "required": ["value"], "additionalProperties": False}
    try:
        for name in ("responses_strict", "responses_invalid_schema", "chat_json_object"):
            started = time.monotonic()
            row = {"case": name}
            try:
                if name.startswith("responses"):
                    target = schema if name == "responses_strict" else {"type": "impossible"}
                    result = await client.responses.create(
                        model=settings.model, input="Write only HELLO, never JSON.",
                        text={"format": {"type": "json_schema", "name": "probe",
                                         "strict": True, "schema": target}},
                        reasoning={"effort": "medium"})
                    row.update(accepted=True, returned_model=result.model, output=result.output_text,
                               usage=result.usage.model_dump() if result.usage else None)
                else:
                    result = await client.chat.completions.create(
                        model=settings.model, messages=[{"role": "user", "content": "Write only HELLO, never JSON."}],
                        response_format={"type": "json_object"}, reasoning_effort="medium")
                    row.update(accepted=True, returned_model=result.model, output=result.choices[0].message.content,
                               usage=result.usage.model_dump() if result.usage else None)
            except Exception as exc:
                row.update(accepted=False, error_type=type(exc).__name__,
                           http_status=getattr(exc, "status_code", None))
            row["duration_ms"] = round((time.monotonic()-started)*1000, 1)
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            (OUT / "provider-alternatives.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2)+"\n")
    finally:
        await client.close()


async def json_mode_checks():
    settings = Settings()
    client = AsyncOpenAI(base_url=settings.api_base, api_key=settings.api_key.get_secret_value(),
                         timeout=settings.request_timeout_seconds, max_retries=0)
    rows = []
    try:
        for repeat in range(2):
            for mode in ("text", "json_object"):
                start = time.monotonic()
                row = {"repeat": repeat, "mode": mode}
                try:
                    result = await client.chat.completions.create(
                        model=settings.model,
                        messages=[{"role": "user", "content": "Do not use JSON. Output exactly HELLO as plain text."}],
                        response_format={"type": mode}, reasoning_effort="medium")
                    output = result.choices[0].message.content
                    row.update(output=output, usage=result.usage.model_dump() if result.usage else None)
                    try:
                        json.loads(output)
                        row["json_valid"] = True
                    except (TypeError, ValueError):
                        row["json_valid"] = False
                except Exception as exc:
                    row.update(error_type=type(exc).__name__, http_status=getattr(exc,"status_code",None))
                row["duration_ms"] = round((time.monotonic()-start)*1000,1)
                rows.append(row)
                print(json.dumps(row), flush=True)
                (OUT / "json-mode-checks.json").write_text(json.dumps(rows,indent=2)+"\n")
    finally:
        await client.close()


async def reasoning_comparison():
    settings = Settings()
    provider = ProviderAdapter(model=settings.model,api_key=settings.api_key.get_secret_value(),
        api_base=settings.api_base,timeout_seconds=settings.request_timeout_seconds,
        response_format="json_object")
    rows = []
    context = {"original_input":"这是工程测试合成情境。会议原定周二，已改到周三下午四点，同事已经确认。请基于最后安排直接给出一个准备建议，不需要追问。",
               "clarification_question":None,"clarification_answer":None,
               "clarification_round":0}
    try:
        for repeat,efforts in enumerate((("none","medium","high"),("high","medium","none"))):
            for effort in efforts:
                metrics = []
                token = provider_metrics_var.set(metrics)
                row = {"repeat":repeat,"effort":effort}
                try:
                    payload = await EmoraAgent(provider).decide(context, reasoning_effort=effort)
                    decision = AgentDecision.model_validate(payload)
                    text = decision.reply
                    row.update(schema_valid=True,
                               updated_day_present="周三" in text,
                               updated_time_present=any(v in text for v in ("四点","4点","16:00")))
                except Exception as exc:
                    row.update(schema_valid=False,error_type=type(exc).__name__,error_code=getattr(exc,"code",None))
                finally:
                    provider_metrics_var.reset(token)
                row["metrics"] = metrics
                rows.append(row)
                print(json.dumps(row,ensure_ascii=False),flush=True)
                (OUT / "reasoning-comparison.json").write_text(json.dumps({
                    "tier":"real_provider_direct_no_guard", "same_prompt":True,
                    "expert_quality_evaluation":False,"cases":rows},ensure_ascii=False,indent=2)+"\n")
    finally:
        await provider.close()


if __name__ == "__main__":
    asyncio.run(reasoning_comparison() if "--reasoning-comparison" in sys.argv else
                json_mode_checks() if "--json-mode-checks" in sys.argv else
                alternatives() if "--alternatives" in sys.argv else main())
