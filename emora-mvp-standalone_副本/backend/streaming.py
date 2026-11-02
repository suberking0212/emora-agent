import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, AsyncIterator

from backend.contracts import AnalysisResponse, AnalysisStatus
from backend.services import BusinessError

logger = logging.getLogger(__name__)


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _display_text(response: AnalysisResponse) -> str:
    if response.status == AnalysisStatus.BLOCKED:
        if response.error_code == "unsafe_input_seek_support":
            return "我很关心你现在的处境。如果你或他人可能正面临现实危险，请联系当地急救服务、危机热线或可信任的人。"
        return "这段内容目前无法继续进行普通分析。"
    if response.status == AnalysisStatus.FAILED:
        return "这次暂时无法完成。你的内容仍保留在当前对话中，可以稍后重新开始。"
    if response.status == AnalysisStatus.WAITING_CLARIFICATION:
        return response.clarification_question or ""

    result = response.result
    if result is None:
        return ""
    pieces = [
        ("情境概括", result.summary),
        ("主要情绪", "、".join(result.emotions)),
        ("核心矛盾", result.conflict),
        ("你可能在意的", result.user_needs),
        ("可以尝试", "\n".join(f"{index}. {item}" for index, item in enumerate(result.suggestions, 1))),
        ("下一步", result.next_step),
        ("安全说明", result.safety_note),
    ]
    return "\n\n".join(f"{heading}\n{value}" for heading, value in pieces if value)


async def analysis_event_stream(
    operation: Callable[[Callable[[str], None], Callable[[str], None]], Awaitable[AnalysisResponse]],
    request_id: str,
) -> AsyncIterator[str]:
    events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

    def report_progress(stage: str) -> None:
        events.put_nowait(("progress", {"stage": stage}))

    def report_analysis(summary: str) -> None:
        for offset in range(0, len(summary), 32):
            events.put_nowait(("analysis", {"text": summary[offset:offset + 32]}))

    yield _event("status", {"message": "正在开始本次分析…"})
    task = asyncio.create_task(operation(report_progress, report_analysis))
    try:
        while not task.done():
            try:
                event, payload = await asyncio.wait_for(events.get(), timeout=15)
                yield _event(event, payload)
            except TimeoutError:
                yield ": keep-alive\n\n"

        while not events.empty():
            event, payload = events.get_nowait()
            yield _event(event, payload)

        response = await task
        text = _display_text(response)
        chunk_size = 96
        for offset in range(0, len(text), chunk_size):
            yield _event("delta", {"text": text[offset:offset + chunk_size]})
        yield _event("complete", response.model_dump(mode="json"))
    except BusinessError as exc:
        yield _event("error", {"code": exc.code})
    except asyncio.CancelledError:
        task.cancel()
        raise
    except Exception as exc:
        logger.error("analysis_stream_failed error_type=%s request_id=%s",
                     type(exc).__name__, request_id)
        yield _event("error", {"code": "internal_error"})
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
