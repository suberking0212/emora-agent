import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, AsyncIterator

from backend.contracts import AnalysisResponse
from backend.services import BusinessError

logger = logging.getLogger(__name__)


def _event(name: str, payload: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def analysis_event_stream(
    operation: Callable[
        [Callable[[str], None], Callable[[str], None]],
        Awaitable[AnalysisResponse],
    ],
    request_id: str,
) -> AsyncIterator[str]:
    events: asyncio.Queue[tuple[str, dict]] = asyncio.Queue()

    def report_progress(stage: str) -> None:
        events.put_nowait(("progress", {"stage": stage}))

    def report_answer(text: str) -> None:
        if text:
            events.put_nowait(("delta", {"text": text}))

    async def run_operation():
        try:
            return await operation(report_progress, report_answer)
        finally:
            events.put_nowait(("operation_done", {}))

    yield _event("status", {"message": "正在准备回复…"})
    task = asyncio.create_task(run_operation())
    try:
        while True:
            try:
                event, payload = await asyncio.wait_for(events.get(), timeout=15)
                if event == "operation_done":
                    break
                yield _event(event, payload)
            except TimeoutError:
                yield ": keep-alive\n\n"

        while not events.empty():
            event, payload = events.get_nowait()
            if event != "operation_done":
                yield _event(event, payload)
        response = await task
        if response.session_id:
            yield _event("session", {"session_id": response.session_id})
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
