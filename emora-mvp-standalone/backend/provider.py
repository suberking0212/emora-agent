from pathlib import Path
import asyncio
from collections.abc import Awaitable, Callable
import logging
import json
import time
from contextvars import ContextVar
from urllib.parse import urlsplit

from openai import AsyncOpenAI, APIConnectionError, APITimeoutError, APIStatusError
from pydantic import BaseModel
from backend.logging_config import request_id_var, session_id_var
from backend.contracts import SessionTitle

logger = logging.getLogger(__name__)
JSONPath = tuple[str | int, ...]
TextStreamHandler = Callable[[JSONPath, str, str], Awaitable[None]]
provider_metrics_var: ContextVar[list | None] = ContextVar("provider_metrics", default=None)


def strict_json_schema(model: type[BaseModel]) -> dict:
    """Wire compatibility only; does not change the domain model or its validators."""
    schema = model.model_json_schema()
    def visit(value):
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(schema)
    return schema


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_constant(value):
    raise ValueError("non-finite JSON number")


def _token_usage(reported):
    def counts(values, keys):
        return {key: values[key] for key in keys
                if type(values.get(key)) is int and values[key] >= 0}
    result = counts(reported, ("prompt_tokens", "completion_tokens", "total_tokens"))
    for key, fields in {
        "prompt_tokens_details": ("cached_tokens", "audio_tokens"),
        "completion_tokens_details": ("reasoning_tokens", "audio_tokens", "accepted_prediction_tokens", "rejected_prediction_tokens"),
    }.items():
        if isinstance(reported.get(key), dict):
            result[key] = counts(reported[key], fields)
    return result


class IncrementalJSONText:
    """Extract string-value deltas from a JSON document as it arrives."""

    def __init__(self):
        self.stack: list[dict] = []
        self.root_state = "value"
        self.mode = "normal"
        self.string_role = ""
        self.string_path: JSONPath = ()
        self.string_key: list[str] = []
        self.escape = False
        self.unicode_digits: list[str] | None = None
        self.high_surrogate: int | None = None
        self.root_started = False

    def _consume_value(self) -> JSONPath | None:
        if not self.stack:
            if self.root_state == "value":
                self.root_state = "done"
                return ()
            return None
        frame = self.stack[-1]
        if frame["kind"] == "object" and frame["state"] == "value":
            path = (*frame["path"], frame["key"])
            frame["key"] = None
            frame["state"] = "comma_or_end"
            return path
        if frame["kind"] == "array" and frame["state"] == "value_or_end":
            path = (*frame["path"], frame["index"])
            frame["index"] += 1
            frame["state"] = "comma_or_end"
            return path
        return None

    def _start_string(self, role: str, path: JSONPath) -> None:
        self.mode = "string"
        self.string_role = role
        self.string_path = path
        self.string_key = []
        self.escape = False
        self.unicode_digits = None
        self.high_surrogate = None

    @staticmethod
    def _flush_delta(events: list, decoded: list[str], path: JSONPath) -> None:
        if decoded:
            events.append(("delta", path, "".join(decoded)))
            decoded.clear()

    def _append_decoded(self, char: str, decoded: list[str]) -> None:
        if self.string_role == "key":
            self.string_key.append(char)
        else:
            decoded.append(char)

    def _append_code_unit(self, code: int, decoded: list[str]) -> None:
        if self.high_surrogate is not None:
            if 0xDC00 <= code <= 0xDFFF:
                scalar = 0x10000 + ((self.high_surrogate - 0xD800) << 10) + (code - 0xDC00)
                self._append_decoded(chr(scalar), decoded)
                self.high_surrogate = None
                return
            self._append_decoded("\ufffd", decoded)
            self.high_surrogate = None
        if 0xD800 <= code <= 0xDBFF:
            self.high_surrogate = code
        elif 0xDC00 <= code <= 0xDFFF:
            self._append_decoded("\ufffd", decoded)
        else:
            self._append_decoded(chr(code), decoded)

    def _normal(self, char: str) -> None:
        if char in " \t\r\n":
            return
        if char == "{":
            path = self._consume_value()
            self.stack.append({"kind": "object", "path": path or (), "state": "key_or_end", "key": None})
        elif char == "[":
            path = self._consume_value()
            self.stack.append({"kind": "array", "path": path or (), "state": "value_or_end", "index": 0})
        elif char == '"' and self.stack and self.stack[-1]["kind"] == "object" \
                and self.stack[-1]["state"] == "key_or_end":
            self._start_string("key", self.stack[-1]["path"])
        elif char == '"':
            path = self._consume_value()
            if path is not None:
                self._start_string("value", path)
        elif char == ":" and self.stack and self.stack[-1]["kind"] == "object":
            if self.stack[-1]["state"] == "colon":
                self.stack[-1]["state"] = "value"
        elif char == "," and self.stack:
            frame = self.stack[-1]
            if frame["state"] == "comma_or_end":
                frame["state"] = "key_or_end" if frame["kind"] == "object" else "value_or_end"
        elif char == "}" and self.stack and self.stack[-1]["kind"] == "object":
            self.stack.pop()
        elif char == "]" and self.stack and self.stack[-1]["kind"] == "array":
            self.stack.pop()
        else:
            path = self._consume_value()
            if path is not None:
                self.mode = "primitive"

    def feed(self, fragment: str) -> list[tuple[str, JSONPath, str]]:
        events: list[tuple[str, JSONPath, str]] = []
        decoded: list[str] = []
        for char in fragment:
            if not self.root_started:
                if char.isspace():
                    continue
                if char != "{":
                    raise ProviderError("provider_json_parse_error")
                self.root_started = True
            if self.mode == "primitive":
                if char in ",]} \t\r\n":
                    self.mode = "normal"
                    self._normal(char)
                continue
            if self.mode == "normal":
                prior_mode = self.mode
                self._normal(char)
                if prior_mode == "normal" and self.mode == "string" and self.string_role == "value":
                    events.append(("start", self.string_path, ""))
                continue

            if self.unicode_digits is not None:
                self.unicode_digits.append(char)
                if len(self.unicode_digits) == 4:
                    try:
                        self._append_code_unit(int("".join(self.unicode_digits), 16), decoded)
                    except ValueError:
                        decoded.append("\ufffd")
                    self.unicode_digits = None
                continue
            if self.escape:
                self.escape = False
                if char == "u":
                    self.unicode_digits = []
                else:
                    self._append_decoded({
                        '"': '"', "\\": "\\", "/": "/", "b": "\b",
                        "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                    }.get(char, "\ufffd"), decoded)
                continue
            if char == "\\":
                self.escape = True
            elif char == '"':
                if self.high_surrogate is not None:
                    self._append_decoded("\ufffd", decoded)
                    self.high_surrogate = None
                if self.string_role == "value":
                    self._flush_delta(events, decoded, self.string_path)
                    events.append(("end", self.string_path, ""))
                else:
                    frame = self.stack[-1]
                    frame["key"] = "".join(self.string_key)
                    frame["state"] = "colon"
                self.mode = "normal"
                self.string_role = ""
            else:
                if self.string_role == "key":
                    self.string_key.append(char)
                else:
                    decoded.append(char)

        if self.mode == "string" and self.string_role == "value":
            self._flush_delta(events, decoded, self.string_path)
        return events


class ProviderError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class ProviderAdapter:
    def __init__(self, *, model: str, api_key: str, api_base: str | None, timeout_seconds: float,
                 response_format: str = "json_object"):
        self.model = model
        if response_format not in {"json_schema", "json_object", "text"}:
            raise ValueError("unsupported response format")
        self.response_format = response_format
        self.provider_host = urlsplit(api_base or "https://api.openai.com/v1").hostname
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self.system_prompt = Path(__file__).parent.joinpath("prompts/system.md").read_text()
        self.session_title_prompt = Path(__file__).parent.joinpath("prompts/session_title.md").read_text()

    async def generate_session_title(self, source: str) -> str:
        payload = await self.complete(
            {"title_source": source[:2_000]},
            SessionTitle,
            reasoning_effort="low",
            system_prompt=self.session_title_prompt,
            format_instruction=(
                "Return exactly one JSON object matching the schema. "
                "Write a concise, neutral title in the same language as the supplied content. "
                "Keep it under 18 visible characters and do not include quotation marks."
            ),
        )
        return SessionTitle.model_validate(payload).title

    async def complete(self, context: dict, response_model: type[BaseModel], *,
                       reasoning_effort: str = "medium",
                       system_prompt: str | None = None,
                       format_instruction: str | None = None,
                       on_text: TextStreamHandler | None = None) -> dict:
        started = time.monotonic()
        metadata = {"request_id": request_id_var.get(), "session_id": session_id_var.get(),
                    "provider": self.provider_host, "requested_model": self.model,
                    "reasoning_effort_requested": reasoning_effort,
                    "response_format": self.response_format, "json_parse": "not_run",
                    "status": "failed", "error_code": None, "usage": None}
        try:
            schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
            format_instruction = format_instruction or (
                "Return exactly one JSON object matching the following JSON Schema. "
                "Do not include Markdown fences, reasoning, or text outside the object. "
                "For ask_clarification, set result to null and provide one clarification_question. "
                "For complete, set clarification_question to null and provide the complete result. "
            )
            current = {key: value for key, value in context.items() if key != "history"}
            request = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": (system_prompt or self.system_prompt) +
                     "\n\n" + format_instruction + "\nSchema:\n" + schema},
                    *context.get("history", []),
                    {"role": "user", "content": json.dumps(current, ensure_ascii=False)},
                ],
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if self.response_format == "json_schema":
                request["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": response_model.__name__, "strict": True,
                    "schema": strict_json_schema(response_model)}}
            elif self.response_format == "json_object":
                request["response_format"] = {"type": "json_object"}
            request["reasoning_effort"] = reasoning_effort
            stream = await self.client.chat.completions.create(**request)
            content_parts = []
            text_parser = IncrementalJSONText() if on_text else None
            finish_reason = None
            first_content_at = None
            content_events = 0
            content_chars = 0
            refused = False
            try:
                async for chunk in stream:
                    metadata["returned_model"] = getattr(chunk, "model", None)
                    metadata["completion_id"] = getattr(chunk, "id", None)
                    usage = getattr(chunk, "usage", None)
                    if usage is not None:
                        metadata["usage"] = _token_usage(usage.model_dump())
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if getattr(choice.delta, "refusal", None):
                        refused = True
                    if choice.delta.content:
                        fragment = choice.delta.content
                        if first_content_at is None:
                            first_content_at = time.monotonic()
                        content_events += 1
                        content_chars += len(fragment)
                        content_parts.append(fragment)
                        if text_parser and on_text:
                            try:
                                events = text_parser.feed(fragment)
                            except ProviderError:
                                # Keep reading refusal/finish metadata; never extract a trailing object.
                                text_parser = None
                                events = []
                            for event, path, text in events:
                                await on_text(path, event, text)
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
            finally:
                await stream.close()
            metadata["finish_reason"] = finish_reason
            metadata["first_content_ms"] = round((first_content_at-started)*1000, 1) if first_content_at else None
            if refused or finish_reason == "content_filter":
                raise ProviderError("provider_model_refusal")
            if finish_reason != "stop":
                raise ProviderError("provider_incomplete_response")
            content = "".join(content_parts)
            if not content.strip():
                raise ProviderError("provider_empty_response")
            try:
                payload = json.loads(content, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            except ValueError as exc:
                metadata["json_parse"] = "failed"
                raise ProviderError("provider_json_parse_error") from exc
            metadata["json_parse"] = "passed"
            if not isinstance(payload, dict):
                raise ProviderError("provider_invalid_response_shape")
            first_content_ms = (first_content_at - started) * 1000 if first_content_at else None
            logger.info(
                "provider_json_parsed duration_ms=%.1f first_content_ms=%s "
                "content_events=%d content_chars=%d",
                (time.monotonic() - started) * 1000,
                f"{first_content_ms:.1f}" if first_content_ms is not None else "none",
                content_events,
                content_chars,
            )
            # Schema/domain validation is performed once by AnalysisService.
            metadata["status"] = "parsed"
            return payload
        except asyncio.CancelledError:
            metadata.update(status="cancelled", error_code="request_cancelled")
            raise
        except ProviderError as exc:
            metadata["error_code"] = exc.code
            if exc.code == "provider_json_parse_error":
                metadata["json_parse"] = "failed"
            raise
        except (APITimeoutError, APIConnectionError, APIStatusError) as exc:
            code = "provider_timeout" if isinstance(exc, APITimeoutError) else \
                "provider_http_error" if isinstance(exc, APIStatusError) else "provider_transport_error"
            metadata.update(error_code=code, http_status=getattr(exc, "status_code", None))
            raise ProviderError(code) from exc
        except Exception as exc:
            logger.warning(
                "provider_request_failed error_type=%s cause_type=%s status=%s duration_ms=%.1f",
                type(exc).__name__, type(exc.__cause__).__name__ if exc.__cause__ else "none",
                getattr(exc, "status_code", None),
                (time.monotonic() - started) * 1000,
            )
            metadata["error_code"] = "provider_protocol_error"
            raise ProviderError("provider_protocol_error") from exc
        finally:
            metadata["duration_ms"] = round((time.monotonic()-started)*1000, 1)
            logger.info("provider_call %s", json.dumps(metadata, ensure_ascii=True))
            collector = provider_metrics_var.get()
            if collector is not None:
                collector.append(metadata)

    async def close(self) -> None:
        await self.client.close()
