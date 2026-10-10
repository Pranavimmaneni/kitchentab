"""Push subscriptions, preferences and the in-app inbox (PRD §20.2), plus the cron and drain
entry points (ADR-0013). Nothing here sends a push: rows go through the drain."""

from __future__ import annotations

import hmac
import html
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import time
from typing import Annotated, Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select, update

from app.adapters.queue import kick_safely
from app.deps import Actor, ctx, require_actor
from app.errors import ErrorCode, Problem
from app.models import Notification, NotificationPref, PushSubscription, User
from app.policy import require_own
from app.services import notify as svc
from app.services.notify_runtime import vapid_keys

router = APIRouter(tags=["notifications"])
log = logging.getLogger("app.cron")


def _deps(request: Request) -> svc.NotifyDeps:
    deps: svc.NotifyDeps = request.app.state.notify
    return deps


@router.get("/push/config")
async def push_config(request: Request) -> dict[str, Any]:
    public, _ = vapid_keys(request.app.state.settings)
    return {"vapid_public_key": public}


def device_label(user_agent: str) -> str:
    ua = user_agent.lower()
    browser = "Edge" if "edg/" in ua else "Firefox" if "firefox" in ua else "Chrome" if "chrome" in ua else "Safari"
    if "iphone" in ua:
        return "This iPhone" if browser == "Safari" else f"{browser} on iPhone"
    if "ipad" in ua:
        return "iPad"
    if "android" in ua:
        return f"{browser} on Android"
    if "mac os" in ua or "macintosh" in ua:
        return f"{browser} on Mac"
    if "windows" in ua:
        return f"{browser} on Windows"
    if "linux" in ua:
        return f"{browser} on Linux"
    return "This browser"


class Keys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class NewSubscription(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000)
    keys: Keys
    device_label: str | None = Field(default=None, max_length=60)
    replaces_endpoint: str | None = Field(default=None, max_length=2000)  # pushsubscriptionchange

    @field_validator("endpoint", "replaces_endpoint")
    @classmethod
    def _https(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("push endpoints are https URLs")
        return value


# The browsers' push services (SEC: the server POSTs to this URL, so only these when deployed).
PUSH_HOSTS = (
    "fcm.googleapis.com",
    "updates.push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)


def allowed_endpoint(endpoint: str, deployed: bool) -> bool:
    if not deployed:
        return True  # local and test: the fake push service on 127.0.0.1
    parts = urlsplit(endpoint)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host == h.lstrip(".") or host.endswith(h) for h in PUSH_HOSTS)


def _sub_view(s: PushSubscription) -> dict[str, Any]:
    return {
        "id": str(s.id),
        "device_label": s.device_label,
        "endpoint_hash": svc_hash(s.endpoint),
        "created_at": s.created_at.isoformat(),
        "last_success_at": s.last_success_at.isoformat() if s.last_success_at else None,
        "stale": s.stale_at is not None,
    }


def svc_hash(endpoint: str) -> str:
    """Clients compare their current subscription by hash; endpoints are capability URLs."""
    import hashlib

    return hashlib.sha256(endpoint.encode()).hexdigest()[:16]


@router.post("/push/subscriptions", status_code=201)
async def subscribe(
    body: NewSubscription, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    """Naturally idempotent on the endpoint (PUSH-03/04): the same endpoint updates in place; a new
    endpoint replacing an old one (pushsubscriptionchange) removes the old row."""
    c = ctx(request)
    now = c.clock.now()
    ua = request.headers.get("user-agent", "")[:300]
    if not allowed_endpoint(body.endpoint, c.settings.is_deployed):
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "That isn't a browser push service")
    async with c.sessions() as db, db.begin():
        if body.replaces_endpoint and body.replaces_endpoint != body.endpoint:
            await db.execute(
                delete(PushSubscription).where(
                    PushSubscription.endpoint == body.replaces_endpoint, PushSubscription.user_id == actor.user.id
                )
            )
        sub = (await db.scalars(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))).first()
        if sub is None:
            sub = PushSubscription(
                id=uuid.uuid4(),
                user_id=actor.user.id,
                endpoint=body.endpoint,
                p256dh=body.keys.p256dh,
                auth=body.keys.auth,
                user_agent=ua,
                device_label=body.device_label or device_label(ua),
                created_at=now,
                failure_count=0,
            )
            db.add(sub)
        else:  # one browser profile, possibly a different person signed in now
            if sub.user_id != actor.user.id and (sub.p256dh, sub.auth) != (body.keys.p256dh, body.keys.auth):
                # Only the browser holding this subscription's keys may move it to another account.
                raise Problem(409, ErrorCode.CONFLICT, "This device is registered to someone else")
            sub.user_id, sub.p256dh, sub.auth = actor.user.id, body.keys.p256dh, body.keys.auth
            sub.user_agent, sub.failure_count, sub.stale_at = ua, 0, None
            sub.device_label = body.device_label or sub.device_label
        await db.flush()
        return _sub_view(sub)


@router.get("/push/subscriptions")
async def subscriptions(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> list[dict[str, Any]]:
    async with ctx(request).read_sessions() as db:
        rows = (
            await db.scalars(
                select(PushSubscription)
                .where(PushSubscription.user_id == actor.user.id)
                .order_by(PushSubscription.created_at)
            )
        ).all()
    return [_sub_view(s) for s in rows]


@router.delete("/push/subscriptions/{sub_id}")
async def unsubscribe(
    sub_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    async with ctx(request).sessions() as db, db.begin():
        owner = await db.scalar(select(PushSubscription.user_id).where(PushSubscription.id == sub_id))
        require_own(owner, actor.user.id)  # SEC-10
        await db.execute(delete(PushSubscription).where(PushSubscription.id == sub_id))
    return {"id": str(sub_id), "deleted": True}


@router.post("/push/test", status_code=202)
async def push_test(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> dict[str, Any]:
    """PUSH-17: queues "It works!" for every device of the caller; the drain sends it."""
    deps = _deps(request)
    notification = await svc.push_test(deps, actor.user.id)
    await kick_safely(request.app.state.kicker, key=f"test-{notification}")
    return {"queued": notification is not None}


class Prefs(BaseModel):
    push: dict[str, bool] = Field(default_factory=dict)
    email: dict[str, bool] = Field(default_factory=dict)
    quiet_hours: bool | None = None
    quiet_start: time | None = None
    quiet_end: time | None = None
    private_lock_screen: bool | None = None

    @field_validator("push", "email")
    @classmethod
    def _types(cls, value: dict[str, bool]) -> dict[str, bool]:
        unknown = set(value) - set(svc.PREF_TYPES)
        if unknown:
            raise ValueError(f"unknown types: {', '.join(sorted(unknown))}")
        return value


def _prefs_view(user: User, prefs: dict[str, dict[str, bool]]) -> dict[str, Any]:
    return {
        "push": prefs["push"],
        "email": prefs["email"],
        "quiet_hours": user.quiet_start is not None and user.quiet_end is not None,
        "quiet_start": user.quiet_start.strftime("%H:%M") if user.quiet_start else "22:00",
        "quiet_end": user.quiet_end.strftime("%H:%M") if user.quiet_end else "08:00",
        "private_lock_screen": user.private_lock_screen,
        "timezone": user.timezone,
    }


@router.get("/me/notification-prefs")
async def get_prefs(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> dict[str, Any]:
    async with ctx(request).read_sessions() as db:
        user = await db.get(User, actor.user.id)
        assert user is not None
        return _prefs_view(user, await svc.prefs_for(db, user.id))


@router.put("/me/notification-prefs")
async def put_prefs(body: Prefs, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> dict[str, Any]:
    """Set-state (PUSH-09): each given type × channel is stored as given; omitted ones keep their value."""
    async with ctx(request).sessions() as db, db.begin():
        user = await db.get(User, actor.user.id, with_for_update={"key_share": True})
        assert user is not None
        for channel, values in (("push", body.push), ("email", body.email)):
            for type_, enabled in values.items():
                row = await db.get(NotificationPref, (user.id, type_, channel))
                if row is None:
                    db.add(NotificationPref(user_id=user.id, type=type_, channel=channel, enabled=enabled))
                else:
                    row.enabled = enabled
        if body.quiet_hours is False:
            user.quiet_start = user.quiet_end = None
        elif body.quiet_hours or body.quiet_start or body.quiet_end:
            user.quiet_start = body.quiet_start or user.quiet_start or time(22, 0)
            user.quiet_end = body.quiet_end or user.quiet_end or time(8, 0)
        if body.private_lock_screen is not None:
            user.private_lock_screen = body.private_lock_screen
        await db.flush()
        return _prefs_view(user, await svc.prefs_for(db, user.id))


@router.get("/me/inbox")
async def get_inbox(
    request: Request, actor: Annotated[Actor, Depends(require_actor)], cursor: str | None = None
) -> dict[str, Any]:
    async with ctx(request).read_sessions() as db:
        return await svc.inbox(db, actor.user.id, cursor, now=ctx(request).clock.now())


class ReadBody(BaseModel):
    ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    all: bool = False
    types: list[str] | None = Field(default=None, max_length=12)
    url: str | None = Field(default=None, max_length=300)  # mark what a screen shows as seen


@router.post("/me/inbox/read")
async def read_inbox(
    body: ReadBody, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    """Set-state: marks rows read (idempotent). The badge clears as things are seen (PUSH-16)."""
    c = ctx(request)
    if not (body.ids or body.all or body.url):
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Say what was read")
    q = update(Notification).where(
        Notification.user_id == actor.user.id, Notification.channel == "inapp", Notification.read_at.is_(None)
    )
    if body.ids:
        q = q.where(Notification.id.in_(body.ids))
    if body.types:
        q = q.where(Notification.type.in_(body.types))
    if body.url:
        q = q.where(Notification.payload["url"].astext == body.url)
    async with c.sessions() as db, db.begin():
        await db.execute(q.values(read_at=c.clock.now()))
        return await svc.inbox(db, actor.user.id, None, limit=0, now=c.clock.now())


# ---- cron and drain (Vercel cron / queue sweeper; bearer CRON_SECRET) ----------------------------


def _require_cron(request: Request, authorization: str | None) -> None:
    secret = request.app.state.settings.cron_secret
    expected = f"Bearer {secret.get_secret_value()}" if secret else None
    if expected is None or not authorization or not hmac.compare_digest(authorization, expected):
        raise Problem(401, ErrorCode.UNAUTHENTICATED, "Not allowed")


@router.get("/cron/daily", include_in_schema=False)
async def cron_daily(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    """The single daily Hobby cron (ADR-0013): expire asks, purge, schedule the 9:00 round-ups, drain."""
    _require_cron(request, authorization)
    from app.routes.receipts import get_storage
    from app.services.backup import export_to_storage
    from app.services.bill_life import auto_remind
    from app.services.inventory import expire_requests
    from app.services.maintenance import purge_expired

    deps = _deps(request)
    now = deps.clock.now()
    try:
        storage = get_storage(request)
    except Problem:  # storage misconfigured: purge the rest, keep receipt files for the next run
        storage = None
    out: dict[str, Any] = {"failed": []}

    async def step(name: str, run: Callable[[], Awaitable[Any]]) -> None:
        """Each step on its own: one failure is logged and reported, the rest still run."""
        try:
            out[name] = await run()
        except Exception:
            log.exception("daily cron step failed", extra={"step": name})
            out[name] = None
            out["failed"].append(name)

    async def expire_and_purge() -> tuple[int, dict[str, int]]:
        expired = await expire_requests(request.app.state.writes, now)
        async with deps.sessions() as db, db.begin():
            return expired, await purge_expired(db, now, storage)

    async def backup() -> dict[str, Any] | None:
        return await export_to_storage(deps.sessions, storage, now) if storage is not None else None

    async def drain_now() -> int:
        return (await svc.drain(deps)).sent

    await step("housekeeping", expire_and_purge)
    await step("backup", backup)  # early: a slow drain must not crowd out the daily copy
    await step("soon", lambda: svc.use_soon_roundup(deps))
    await step("digests", lambda: svc.weekly_digest(deps))
    await step("auto_reminders", lambda: auto_remind(request.app.state.writes, now))
    await step("sent", drain_now)
    house = out.pop("housekeeping")
    out["expired_asks"], out["purged"] = house if house is not None else (None, None)
    return out


@router.post("/cron/drain", include_in_schema=False)
async def drain(request: Request, authorization: Annotated[str | None, Header()] = None) -> dict[str, Any]:
    _require_cron(request, authorization)
    result = await svc.drain(_deps(request))
    return {"outbox": result.outbox, "notifications": result.notifications, "sent": result.sent}


def _unsubscribe_page(title: str, body: str, form: str = "") -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<meta name="color-scheme" content="light dark"><title>KitchenTab</title></head>
<body style="font:16px/1.5 system-ui,sans-serif;max-width:420px;margin:48px auto;padding:0 16px">
<h1 style="font-size:22px">{html.escape(title)}</h1><p>{html.escape(body)}</p>{form}</body></html>"""
    )


@router.get("/email/unsubscribe", include_in_schema=False)
async def unsubscribe_page(u: uuid.UUID, t: str, s: str) -> HTMLResponse:
    """A confirm page: link scanners follow GETs, so only the POST changes anything (EMAIL-02)."""
    label = svc.TYPE_LABELS.get(t, "these emails")
    form = (
        f'<form method="post" action="/api/v1/email/unsubscribe?u={u}&amp;t={html.escape(t)}&amp;s={html.escape(s)}">'
        '<button style="min-height:44px;padding:0 20px;font:inherit;font-weight:700">Stop these emails</button></form>'
    )
    return _unsubscribe_page(f"Stop emails about {label}?", "Push and the in-app inbox stay as they are.", form)


@router.post("/email/unsubscribe", include_in_schema=False)
async def email_unsubscribe(u: uuid.UUID, t: str, s: str, request: Request) -> HTMLResponse:
    """RFC 8058 one-click (List-Unsubscribe-Post) and the confirm page's button. Set-state: repeatable."""
    c = ctx(request)
    async with c.sessions() as db, db.begin():
        ok = await svc.unsubscribe(db, c.settings.token_hash_pepper.get_secret_value(), u, t, s)
    if not ok:
        raise Problem(404, ErrorCode.NOT_FOUND, "That link doesn't work")
    return _unsubscribe_page("Done", f"No more emails about {svc.TYPE_LABELS[t]}. Turn them back on in Settings.")
