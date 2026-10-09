"""RFC 9457 problem details with a stable `code` field (PRD §11, §21.8)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.logs import request_id_var

PROBLEM_JSON = "application/problem+json"


class ErrorCode(StrEnum):
    # Sync and state (§21.8)
    VERSION_CONFLICT = "VERSION_CONFLICT"
    BILL_SETTLED = "BILL_SETTLED"
    BILL_VOIDED = "BILL_VOIDED"
    LAST_PERSON = "LAST_PERSON"
    ALREADY_ANSWERED = "ALREADY_ANSWERED"
    ALREADY_CLAIMED = "ALREADY_CLAIMED"
    ITEM_GONE = "ITEM_GONE"
    NOT_A_MEMBER = "NOT_A_MEMBER"
    IDEMPOTENCY_MISMATCH = "IDEMPOTENCY_MISMATCH"
    STALE_CLIENT = "STALE_CLIENT"
    RATE_LIMITED = "RATE_LIMITED"
    # Sign-in and links
    CODE_EXPIRED = "CODE_EXPIRED"
    CODE_LOCKED = "CODE_LOCKED"
    CODE_MISMATCH = "CODE_MISMATCH"
    CAPTCHA_REQUIRED = "CAPTCHA_REQUIRED"
    METHOD_DISABLED = "METHOD_DISABLED"
    EMAIL_UNVERIFIED = "EMAIL_UNVERIFIED"
    IDENTITY_TAKEN = "IDENTITY_TAKEN"
    LAST_SIGN_IN_METHOD = "LAST_SIGN_IN_METHOD"
    LINK_EXPIRED = "LINK_EXPIRED"
    # Houses
    DUPLICATE_NAME = "DUPLICATE_NAME"
    HOUSE_FULL = "HOUSE_FULL"
    ALREADY_MEMBER = "ALREADY_MEMBER"
    PAYER_ON_OPEN_BILLS = "PAYER_ON_OPEN_BILLS"
    ADMIN_REQUIRED = "ADMIN_REQUIRED"
    # Request shape
    IF_MATCH_REQUIRED = "IF_MATCH_REQUIRED"
    IDEMPOTENCY_KEY_REQUIRED = "IDEMPOTENCY_KEY_REQUIRED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    # Generic
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    GONE = "GONE"
    CONFLICT = "CONFLICT"
    INTERNAL = "INTERNAL"


class Problem(Exception):
    """Raise from services; rendered as application/problem+json."""

    def __init__(
        self,
        status: int,
        code: ErrorCode,
        title: str,
        detail: str | None = None,
        *,
        extra: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.extra = extra or {}
        self.headers = headers or {}

    def body(self, instance: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": f"https://kitchentab.app/problems/{self.code.lower().replace('_', '-')}",
            "title": self.title,
            "status": self.status,
            "code": self.code.value,
        }
        if self.detail:
            out["detail"] = self.detail
        if instance:
            out["instance"] = instance
        rid = request_id_var.get()
        if rid:
            out["request_id"] = rid
        out.update(self.extra)
        return out


_STATUS_CODES = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    410: ErrorCode.GONE,
    429: ErrorCode.RATE_LIMITED,
}


def _response(problem: Problem, request: Request) -> JSONResponse:
    return JSONResponse(
        problem.body(instance=request.url.path),
        status_code=problem.status,
        media_type=PROBLEM_JSON,
        headers=problem.headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(request: Request, exc: Problem) -> JSONResponse:
        return _response(exc, request)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, ErrorCode.INTERNAL if exc.status_code >= 500 else ErrorCode.CONFLICT)
        title = str(exc.detail) if exc.detail else "Request failed"
        return _response(Problem(exc.status_code, code, title, headers=dict(exc.headers or {})), request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg", "")} for e in exc.errors()]
        return _response(
            Problem(422, ErrorCode.VALIDATION_FAILED, "Some fields need fixing", extra={"errors": errors}),
            request,
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        import logging

        logging.getLogger("app.errors").exception("unhandled error")
        return _response(Problem(500, ErrorCode.INTERNAL, "Something jammed. Try again."), request)
