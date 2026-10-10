"""`GET /api/test/inbox` (ADR-0016): read emails captured with EMAIL_PROVIDER=testinbox.

Exists in production only until `make set-email` switches to Gmail. Guarded by `X-Test-Secret`
(64+ chars, constant-time compare). Returns 404 for every other email provider so the route is
indistinguishable from absent.
"""

from __future__ import annotations

import hmac
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.deps import ctx
from app.errors import ErrorCode, Problem
from app.models import TestInboxMessage

router = APIRouter(prefix="/api/test", tags=["test-inbox"], include_in_schema=False)


@router.get("/inbox")
async def inbox(request: Request, to: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    c = ctx(request)
    secret = c.settings.test_inbox_secret
    if c.settings.email_provider != "testinbox" or secret is None:
        raise Problem(404, ErrorCode.NOT_FOUND, "Not found")
    given = request.headers.get("X-Test-Secret", "")
    if not hmac.compare_digest(given.encode(), secret.get_secret_value().encode()):
        raise Problem(401, ErrorCode.UNAUTHENTICATED, "Not found")
    async with c.sessions() as db:
        q = select(TestInboxMessage).where(TestInboxMessage.subject != "sms")
        if to:
            q = q.where(TestInboxMessage.to_address == to.strip().lower())
        rows = (await db.scalars(q.order_by(TestInboxMessage.created_at.desc()).limit(max(1, min(limit, 100))))).all()
    return [
        {
            "to": r.to_address,
            "subject": r.subject,
            "text": r.text_body,
            "html": r.html_body,
            "headers": r.headers,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
