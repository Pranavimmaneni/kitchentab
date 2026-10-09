"""Structured JSON logs with a request id, and redaction of secrets (CLAUDE.md rule 8).

Never log secrets, one-time codes, tokens, push keys, full emails or phone numbers. The filter
below is a safety net; call sites must not pass such values in the first place.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)

_SENSITIVE_KEYS = re.compile(
    r"(pass(word)?|secret|token|code|otp|cookie|authorization|auth|p256dh|endpoint|key|email|phone)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_SIX_DIGITS = re.compile(r"(?<!\d)\d{6}(?!\d)")
_BEARERISH = re.compile(r"\b[A-Za-z0-9_-]{22,}\b")

REDACTED = "[redacted]"


def redact_text(value: str) -> str:
    value = _EMAIL.sub(r"\1***@\2", value)
    value = _PHONE.sub(REDACTED, value)
    value = _SIX_DIGITS.sub(REDACTED, value)
    return _BEARERISH.sub(REDACTED, value)


def redact(value: Any, key: str | None = None) -> Any:
    if key is not None and _SENSITIVE_KEYS.search(key):
        return REDACTED
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": redact_text(record.getMessage()),
        }
        rid = request_id_var.get()
        if rid:
            out["request_id"] = rid
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        if extras:
            out.update(redact(extras))
        if record.exc_info:
            out["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(out, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("uvicorn.access",):
        logging.getLogger(noisy).handlers[:] = [handler]
        logging.getLogger(noisy).propagate = False
