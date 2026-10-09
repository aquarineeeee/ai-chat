from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        super().__init__(message)


def error_payload(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message, exc.details))

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{key: value for key, value in error.items() if key != "ctx"} for error in exc.errors()]
        return JSONResponse(
            status_code=422,
            content=error_payload("VALIDATION_ERROR", "请求参数错误", errors),
        )

    @app.exception_handler(OperationalError)
    async def database_error_handler(_: Request, exc: OperationalError) -> JSONResponse:
        if getattr(exc.orig, "args", (None,))[0] in {1205, 1213}:
            return JSONResponse(status_code=409, content=error_payload("RESOURCE_BUSY", "操作发生并发冲突，请稍后重试"))
        return JSONResponse(status_code=500, content=error_payload("INTERNAL_ERROR", "数据库暂不可用"))

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_payload("INTERNAL_ERROR", "未预期的服务端错误"),
        )
