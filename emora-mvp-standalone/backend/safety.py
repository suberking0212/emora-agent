import re
from ipaddress import ip_address
from urllib.parse import urlsplit

from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from backend.contracts import Qwen3GuardResult, QwenSafetyCategory, QwenSafetyLevel, QwenSafetyPhase


class GuardUnavailableError(RuntimeError):
    pass


class GuardProtocolError(RuntimeError):
    pass


class Qwen3GuardClient:
    def __init__(self, *, base_url: str, api_key: str, model_id: str, timeout_seconds: float):
        self.model_id = model_id
        hostname = urlsplit(base_url).hostname
        try:
            loopback = ip_address(hostname or "").is_loopback
        except ValueError:
            loopback = hostname == "localhost"
        # A local model must never be sent through an OS/environment proxy.
        self.client = AsyncOpenAI(
            base_url=base_url, api_key=api_key, timeout=timeout_seconds, max_retries=0,
            http_client=DefaultAsyncHttpxClient(trust_env=not loopback),
        )

    async def check_input(self, text: str) -> Qwen3GuardResult:
        return await self._classify([{"role": "user", "content": text}], QwenSafetyPhase.INPUT)

    async def check_output(self, user_prompt: str, candidate: str) -> Qwen3GuardResult:
        return await self._classify([
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": candidate},
        ], QwenSafetyPhase.OUTPUT)

    async def _classify(self, messages: list[dict[str, str]], phase: QwenSafetyPhase) -> Qwen3GuardResult:
        try:
            response = await self.client.chat.completions.create(model=self.model_id, messages=messages)
        except Exception as exc:
            raise GuardUnavailableError("guard_request_failed") from exc
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise GuardProtocolError("invalid_guard_response_shape") from exc
        if not isinstance(content, str) or not content.strip():
            raise GuardProtocolError("empty_guard_response")
        return parse_qwen3guard_output(content, phase=phase, model_id=self.model_id)

    async def close(self) -> None:
        await self.client.close()


def parse_qwen3guard_output(content: str, *, phase: QwenSafetyPhase, model_id: str) -> Qwen3GuardResult:
    safety_lines = re.findall(r"(?m)^Safety:\s*(Safe|Controversial|Unsafe)\s*$", content)
    category_lines = re.findall(r"(?m)^Categories:\s*(.*?)\s*$", content)
    refusal_lines = re.findall(r"(?m)^Refusal:\s*(Yes|No)\s*$", content)
    if len(safety_lines) != 1 or len(category_lines) != 1:
        raise GuardProtocolError("invalid_guard_response_shape")
    if len(refusal_lines) > 1 or (phase == QwenSafetyPhase.OUTPUT and len(refusal_lines) != 1):
        raise GuardProtocolError("invalid_refusal_field")
    names = {item.value: item for item in QwenSafetyCategory}
    pattern = re.compile("|".join(re.escape(name) for name in sorted(names, key=len, reverse=True)))
    category_text = category_lines[0].strip()
    found = pattern.findall(category_text)
    residue = pattern.sub("", category_text).strip(" ,;|\t")
    if not found or residue or ("None" in found and found != ["None"]):
        raise GuardProtocolError("unknown_guard_category")
    if phase == QwenSafetyPhase.OUTPUT and "Jailbreak" in found:
        raise GuardProtocolError("input_only_category_in_output")
    return Qwen3GuardResult(
        phase=phase,
        safety=QwenSafetyLevel(safety_lines[0]),
        categories=[names[name] for name in found],
        refusal=refusal_lines[0] if refusal_lines else None,
        model_id=model_id,
    )
