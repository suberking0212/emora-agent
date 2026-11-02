from pathlib import Path
import logging
import json
import time

from openai import AsyncOpenAI
from pydantic import BaseModel

logger = logging.getLogger(__name__)
DEEP_THINKING_MAX_COMPLETION_TOKENS = 4096


class PublicAnalysisBrief(BaseModel):
    summary: str


PUBLIC_ANALYSIS_PROMPT = (
    "为情绪支持对话生成面向用户的分析提要。用两三句中文回应用户明确表达的感受，概括已知事实与尚不确定之处，并说明正式回复会如何提供帮助。语气温和具体，不把假设说成事实，不诊断，不编造个人背景，也不输出私有推理过程或隐藏思维链。只返回符合给定 JSON Schema 的对象。"
)


class ProviderError(RuntimeError):
    pass


class ProviderAdapter:
    def __init__(self, *, model: str, api_key: str, api_base: str | None, timeout_seconds: float):
        self.model = model
        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            timeout=timeout_seconds,
            max_retries=0,
        )
        self.system_prompt = Path(__file__).parent.joinpath("prompts/system.md").read_text()

    async def complete(self, context: dict, response_model: type[BaseModel], *,
                       deep_thinking: bool = False,
                       reasoning_effort: str = "medium",
                       system_prompt: str | None = None,
                       format_instruction: str | None = None) -> dict:
        started = time.monotonic()
        try:
            # This gateway documents ordinary chat completions, not native
            # json_schema response_format. Keep the wire request within that
            # contract and describe the required object in the trusted prompt.
            schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
            format_instruction = format_instruction or (
                "Return exactly one JSON object matching the following JSON Schema. "
                "Do not include Markdown fences, reasoning, or text outside the object. "
                "For ask_clarification, set result to null and provide one clarification_question. "
                "For complete, set clarification_question to null and provide the complete result. "
            )
            deep_thinking_prompt = ""
            if deep_thinking:
                deep_thinking_prompt = (
                    "\n\nDeep-thinking mode is enabled. Carefully examine the stated facts, "
                    "plausible interpretations, missing context, emotional needs, and practical "
                    "low-risk options. Use only personal details present in the supplied context; "
                    "separate facts from hypotheses and explain how relevant factors may interact. "
                    "Make the existing result fields more specific and useful: prioritize a small "
                    "set of concrete suggestions, give brief reasons where helpful, and make the "
                    "next step easy to act on. Keep uncertainty explicit; do not diagnose, assert "
                    "unsupported causes, or amplify concern beyond the evidence. If essential "
                    "information is missing, ask exactly one focused clarification question as "
                    "allowed by the current round. Do not add schema fields or reveal chain-of-thought, "
                    "scratch work, or private deliberation; return only the requested final JSON."
                )
            request = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": (system_prompt or self.system_prompt) + deep_thinking_prompt +
                     "\n\n" + format_instruction + "\nSchema:\n" + schema},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                "stream": True,
            }
            request["reasoning_effort"] = reasoning_effort
            if deep_thinking:
                request["max_completion_tokens"] = DEEP_THINKING_MAX_COMPLETION_TOKENS
            stream = await self.client.chat.completions.create(**request)
            content_parts = []
            finish_reason = None
            try:
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    if choice.delta.content:
                        content_parts.append(choice.delta.content)
                    if choice.finish_reason is not None:
                        finish_reason = choice.finish_reason
            finally:
                await stream.close()
            if finish_reason != "stop":
                raise ProviderError("provider_incomplete_response")
            content = "".join(content_parts)
            if not content.strip():
                raise ProviderError("provider_empty_response")
            payload = json.loads(content)
            if not isinstance(payload, dict):
                raise ProviderError("provider_invalid_response_shape")
            logger.info("provider_request_completed duration_ms=%.1f",
                        (time.monotonic() - started) * 1000)
            # Schema/domain validation is performed once by AnalysisService.
            return payload
        except ProviderError:
            raise
        except Exception as exc:
            logger.warning(
                "provider_request_failed error_type=%s cause_type=%s status=%s duration_ms=%.1f",
                type(exc).__name__, type(exc.__cause__).__name__ if exc.__cause__ else "none",
                getattr(exc, "status_code", None),
                (time.monotonic() - started) * 1000,
            )
            raise ProviderError("provider_request_failed") from exc

    async def close(self) -> None:
        await self.client.close()
