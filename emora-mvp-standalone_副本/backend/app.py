import inspect
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from traceback import format_tb

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from backend.config import Settings
from backend.contracts import (
    AnalysisListResponse, AnalysisRequest, AnalysisResponse, ClarificationRequest,
    DEFAULT_REASONING_EFFORT, HealthResponse, REASONING_EFFORTS, RuntimeInfoResponse,
)
from backend.logging_config import configure_logging, install_request_logging
from backend.provider import ProviderAdapter
from backend.safety import Qwen3GuardClient
from backend.services import AnalysisService, BusinessError
from backend.storage import Database
from backend.streaming import analysis_event_stream

configure_logging()
logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
templates = Jinja2Templates(directory=FRONTEND_DIR / "templates")


async def close_resource(resource):
    if resource is None:
        return
    close = getattr(resource, "close", None) or getattr(resource, "dispose", None)
    if close is None:
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logger.error("resource_close_failed error_type=%s traceback=%s",
                     type(exc).__name__, "".join(format_tb(exc.__traceback__)))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = provider = guard = service = None
        try:
            database = Database(settings.database_url)
            database.init_db()
            provider = ProviderAdapter(
                model=settings.model, api_key=settings.api_key.get_secret_value(),
                api_base=settings.api_base, timeout_seconds=settings.request_timeout_seconds,
            )
            guard = Qwen3GuardClient(
                base_url=settings.qwen_guard_base_url,
                api_key=settings.qwen_guard_api_key.get_secret_value(),
                model_id=settings.qwen_guard_model,
                timeout_seconds=settings.qwen_guard_timeout_seconds,
            )
            from backend.agent import EmoraAgent, EmoraAgentGraph
            graph = EmoraAgentGraph(EmoraAgent(provider))
            service = AnalysisService(database=database, provider=provider, guard=guard,
                                      settings=settings, graph=graph)
            app.state.database = database
            app.state.provider = provider
            app.state.guard = guard
            app.state.agent_graph = graph
            app.state.service = service
            logger.info("application_started")
            yield
        except Exception as exc:
            logger.error("application_lifecycle_failed error_type=%s traceback=%s",
                         type(exc).__name__, "".join(format_tb(exc.__traceback__)))
            raise
        finally:
            await close_resource(service)
            await close_resource(locals().get("graph"))
            await close_resource(guard)
            await close_resource(provider)
            await close_resource(database)
            logger.info("application_resources_released")

    app = FastAPI(title="Emora MVP", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
    install_request_logging(app)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html")

    @app.get("/health", response_model=HealthResponse)
    async def health():
        return {"status": "ok"}

    @app.get("/api/runtime", response_model=RuntimeInfoResponse)
    async def runtime_info(request: Request):
        return {
            "model_name": request.app.state.provider.model,
            "reasoning_efforts": list(REASONING_EFFORTS),
            "default_reasoning_effort": DEFAULT_REASONING_EFFORT,
        }

    @app.post("/api/analyses", response_model=AnalysisResponse)
    async def create_analysis(body: AnalysisRequest, request: Request):
        kwargs = {"deep_thinking": body.deep_thinking}
        if body.reasoning_effort != DEFAULT_REASONING_EFFORT:
            kwargs["reasoning_effort"] = body.reasoning_effort
        return await request.app.state.service.create(body.text, **kwargs)

    @app.post("/api/analyses/stream")
    async def create_analysis_stream(body: AnalysisRequest, request: Request):
        service = request.app.state.service
        kwargs = {"deep_thinking": body.deep_thinking}
        if body.reasoning_effort != DEFAULT_REASONING_EFFORT:
            kwargs["reasoning_effort"] = body.reasoning_effort
        return StreamingResponse(
            analysis_event_stream(lambda progress, analysis: service.create(
                                      body.text, **kwargs,
                                      progress=progress, analysis_summary=analysis),
                                  getattr(request.state, "request_id", "-")),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/analyses", response_model=AnalysisListResponse)
    async def list_analyses(request: Request):
        return {"items": request.app.state.service.list()}

    @app.get("/api/analyses/{analysis_id}", response_model=AnalysisResponse)
    async def get_analysis(analysis_id: str, request: Request):
        return request.app.state.service.get(analysis_id)

    @app.post("/api/analyses/{analysis_id}/clarify", response_model=AnalysisResponse)
    async def clarify(analysis_id: str, body: ClarificationRequest, request: Request):
        kwargs = {"deep_thinking": body.deep_thinking}
        if body.reasoning_effort is not None:
            kwargs["reasoning_effort"] = body.reasoning_effort
        return await request.app.state.service.continue_with_answer(
            analysis_id, body.answer, **kwargs
        )

    @app.post("/api/analyses/{analysis_id}/clarify/stream")
    async def clarify_stream(analysis_id: str, body: ClarificationRequest, request: Request):
        service = request.app.state.service
        kwargs = {"deep_thinking": body.deep_thinking}
        if body.reasoning_effort is not None:
            kwargs["reasoning_effort"] = body.reasoning_effort
        return StreamingResponse(
            analysis_event_stream(
                lambda progress, analysis: service.continue_with_answer(
                    analysis_id, body.answer, **kwargs,
                    progress=progress, analysis_summary=analysis
                ),
                getattr(request.state, "request_id", "-"),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.exception_handler(BusinessError)
    async def handle_business_error(request: Request, exc: BusinessError):
        request_id = getattr(request.state, "request_id", "-")
        logger.warning("business_error code=%s request_id=%s", exc.code, request_id)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code},
                            headers={"X-Request-ID": request_id})

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", "-")
        logger.error("unexpected_application_error error_type=%s request_id=%s traceback=%s",
                     type(exc).__name__, request_id, "".join(format_tb(exc.__traceback__)),
                     extra={"request_id": request_id})
        return JSONResponse(status_code=500, content={"error": "internal_error"},
                            headers={"X-Request-ID": request_id})

    return app


app = create_app()
