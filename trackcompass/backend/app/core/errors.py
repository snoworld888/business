"""统一错误码与响应封装。

对齐技术文档 §6.1 通用规范：JSON 格式、统一错误码表。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

T = TypeVar("T")

ERROR_CODES: dict[int, str] = {
    200: "成功",
    400: "参数错误",
    401: "未认证",
    403: "无权限",
    404: "资源不存在",
    429: "限流",
    500: "服务器错误",
}


class ApiError(Exception):
    def __init__(self, status_code: int, detail: str, code: str | None = None):
        self.status_code = status_code
        self.detail = detail
        self.code = code or str(status_code)
        super().__init__(detail)


class Envelope(BaseModel, Generic[T]):
    code: int = 200
    message: str = "成功"
    data: T | None = None


def ok(data: Any = None, message: str = "成功") -> dict[str, Any]:
    return {"code": 200, "message": message, "data": data}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):  # pragma: no cover
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": int(exc.code) if exc.code.isdigit() else 400,
                     "message": exc.detail, "data": None},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code,
                     "message": str(exc.detail) or ERROR_CODES.get(exc.status_code, "错误"),
                     "data": None},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "参数错误", "data": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):  # pragma: no cover
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": f"服务器错误: {exc}", "data": None},
        )
