"""结构化日志：对齐技术文档 §7.4 可观测性（Loki + Jaeger 的本地等价物）。"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": trace_id_var.get(),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TraceMiddleware:
    """为每个请求注入 trace_id 并记录耗时（Jaeger 的轻量替代）。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        trace_id = dict(scope.get("headers") or {}).get(b"x-trace-id")
        trace_id = trace_id.decode() if trace_id else uuid.uuid4().hex[:16]
        token = trace_id_var.set(trace_id)
        started = time.perf_counter()

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                headers.append((b"x-trace-id", trace_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            cost = (time.perf_counter() - started) * 1000
            logging.getLogger("trackcompass.access").info(
                "%s %s %.1fms", scope.get("method"), scope.get("path"), cost
            )
            trace_id_var.reset(token)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").disabled = True
