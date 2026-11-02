import logging
import logging.config
from contextvars import ContextVar
from time import perf_counter
from traceback import format_tb
from uuid import uuid4

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        if not getattr(record, "request_id", None):
            record.request_id = request_id_var.get()
        return True


def configure_logging():
    logging.config.dictConfig({
        "version": 1, "disable_existing_loggers": False,
        "filters": {"request_id": {"()": RequestIdFilter}},
        "formatters": {"standard": {"format": "%(asctime)s %(levelname)s %(name)s request_id=%(request_id)s %(message)s"}},
        "handlers": {"stderr": {"class": "logging.StreamHandler", "formatter": "standard", "filters": ["request_id"]}},
        "root": {"level": "INFO", "handlers": ["stderr"]},
    })


def install_request_logging(app):
    @app.middleware("http")
    async def log_request(request, call_next):
        request_id = uuid4().hex
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logging.getLogger("emora.http").info(
                "http_request_completed method=%s path=%s status=%s duration_ms=%.1f",
                request.method, request.url.path, response.status_code,
                (perf_counter() - started) * 1000,
            )
            return response
        finally:
            request_id_var.reset(token)
