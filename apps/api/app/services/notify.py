"""Notification pipeline (PRD §20.2): outbox → fan-out → `notifications` rows → delivery.

    committed write ── outbox row (same transaction) ──▶ drain()
        fan_out(): recipients per kind, never the actor; one row per recipient × channel with
                   `dedupe_key = house:seq:kind:user:channel` (ON CONFLICT DO NOTHING, PUSH-13)
                   in-app rows are the inbox and are "sent" at once; push and email rows wait for
                   quiet hours ("held") and share changes wait 10 minutes to coalesce (PUSH-10/11)
        deliver(): due push/email rows; private lock screen, 50¢ threshold, daily cap of 20 pushes,
                   held rows grouped per type, retries 1 m, 5 m, 30 m, 2 h then failed

Nothing here runs inside a request handler: the API only kicks the queue after commit.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import time as time_module
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.email import Email, EmailSender
from app.adapters.push import PushResult, SubscriptionInfo, Urgency
from app.clock import Clock
from app.domain.kitchen import short_name
from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.models import (
    ActivityEvent,
    Bill,
    House,
    HouseEvent,
    InventoryItem,
    InventoryOwner,
    Item,
    Member,
    Notification,
    NotificationPref,
    OutboxRow,
    Payment,
    PushSubscription,
    ShareRequest,
    User,
)

log = logging.getLogger("app.notify")

# Defaults from the N2 mockup (NotifySettings.dc.html). `joined` goes to admins only.
PREF_TYPES = ("asks", "answers", "share", "tab", "pay", "remind", "soon", "settled", "joined")
DEFAULTS: dict[str, dict[str, bool]] = {
    "push": {
        "asks": True,
        "answers": True,
        "share": True,
        "tab": True,
        "pay": True,
        "remind": True,
        "soon": True,
        "settled": False,
        "joined": True,
    },
    "email": {
        "asks": False,
        "answers": False,
        "share": True,
        "tab": True,
        "pay": True,
        "remind": False,
        "soon": False,
        "settled": True,
        "joined": False,
    },
}
COALESCE = timedelta(minutes=10)
DRAIN_BUDGET_SECONDS = 240  # stop well inside Vercel's 300 s function limit and schedule a follow-up
SHARE_THRESHOLD_CENTS = 50
DAILY_PUSH_CAP = 20
RETRIES = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2))
OUTBOX_RETRIES = 5
TTL_DEFAULT = 24 * 3600
TTL_ASKS = 48 * 3600
HIGH_URGENCY = {"asks", "pay"}
GROUPED = {  # quiet-hours morning summary per type, when more than one is waiting
    "asks": "{n} asks for your food",
    "answers": "{n} answers to your asks",
    "share": "{n} of your shares changed",
    "tab": "{n} new tabs include you",
    "pay": "{n} payment updates",
    "remind": "{n} reminders to pay",
    "soon": "{n} things need eating soon",
    "settled": "{n} tabs settled or reopened",
    "joined": "{n} people joined your house",
    "test": "It works!",
}
PRIVATE = {
    "asks": "Someone asked for something of yours",
    "answers": "Someone answered your ask",
    "share": "One of your shares changed",
    "tab": "A new tab includes you",
    "pay": "A payment needs a look",
    "remind": "A friendly nudge about a tab",
    "soon": "Some food needs eating soon",
    "settled": "A tab changed state",
    "joined": "Someone joined your house",
    "test": "It works!",
}


def money(cents: int) -> str:
    whole, part = divmod(abs(cents), 100)
    return f"{'−' if cents < 0 else ''}${whole:,}.{part:02d}"


def signed(cents: int) -> str:
    return f"+{money(cents)}" if cents > 0 else money(cents)


# ---- adapters ---------------------------------------------------------------------------------------


class PushSender(Protocol):
    async def send(
        self, sub: SubscriptionInfo, payload: dict[str, Any], *, ttl: int, urgency: Urgency, topic: str | None = None
    ) -> PushResult: ...


@dataclass
class NotifyDeps:
    sessions: async_sessionmaker[AsyncSession]
    clock: Clock
    push: PushSender | None
    email: EmailSender | None
    web_origin: str
    schedule: Callable[[datetime], Awaitable[None]] | None = None  # wake a delayed drain (queue delay)
    unsubscribe_secret: str = ""
    postal_address: str = ""


# ---- recipients -------------------------------------------------------------------------------------


@dataclass
class Message:
    user_id: uuid.UUID
    type: str
    title: str
    body: str
    url: str
    tag: str
    meta: dict[str, Any] = field(default_factory=dict)
    group: str | None = None  # coalescing key (share changes per bill)
    channels: tuple[str, ...] | None = None  # fixed channels (the weekly digest is email only)


@dataclass
class EventCtx:
    db: AsyncSession
    event: HouseEvent
    house: House
    members: dict[uuid.UUID, Member]
    actor_user_id: uuid.UUID | None
    bill: Bill | None

    @property
    def data(self) -> dict[str, Any]:
        return self.event.data or {}

    def user_of(self, member_id: uuid.UUID | str | None) -> uuid.UUID | None:
        if member_id is None:
            return None
        m = self.members.get(uuid.UUID(str(member_id)))
        if m is None or m.status != "active" or m.user_id is None or m.user_id == self.actor_user_id:
            return None  # never the actor (PUSH-07), never departed or unclaimed members
        return m.user_id

    def name(self, member_id: uuid.UUID | str | None) -> str:
        m = self.members.get(uuid.UUID(str(member_id))) if member_id else None
        return m.display_name if m else "Someone"

    @property
    def actor_name(self) -> str:
        return self.name(self.event.actor_member_id)

    @property
    def store(self) -> str:
        return (self.bill.store_name if self.bill else None) or "Tab"


def _member_keys(data: dict[str, int]) -> list[uuid.UUID]:
    return [uuid.UUID(k[2:]) for k in data if k.startswith("m:")]


async def _bill_members(ctx: EventCtx) -> set[uuid.UUID]:
    from app.services.bills import latest_snapshot

    assert ctx.bill is not None
    shares = await latest_snapshot(ctx.db, ctx.bill.id)
    return {m for m in _member_keys(shares) if shares[f"m:{m}"]} | {ctx.bill.payer_member_id}


async def _asks(ctx: EventCtx) -> list[Message]:
    req = await ctx.db.get(ShareRequest, uuid.UUID(ctx.data["id"]))
    if req is None:
        return []
    item = await ctx.db.get(InventoryItem, req.inventory_item_id)
    assert item is not None
    owners = (
        await ctx.db.scalars(select(InventoryOwner.member_id).where(InventoryOwner.inventory_item_id == item.id))
    ).all()
    amount = {"a_bit": "A bit", "half": "Half", "all": "All of it"}[req.amount]
    note = (req.note or "").strip()
    if note:
        note = note[0].lower() + note[1:]
        amount = f"{amount}, {note}" + ("" if note[-1] in ".!?" else ".")
    else:
        amount += "."
    title = f"{ctx.name(req.requester_member_id)} asked for your {item.short_name.lower()}"
    return [
        Message(u, "asks", title, f"{amount} Tap to answer.", f"/kitchen/requests?id={req.id}", f"ask-{req.id}")
        for u in {ctx.user_of(o) for o in owners} - {None}
        if u is not None
    ]


async def _answers(ctx: EventCtx) -> list[Message]:
    status, resolution = ctx.data.get("status"), ctx.data.get("resolution")
    if status not in ("approved", "declined"):
        return []  # undo puts it back to pending: nothing to tell
    user = ctx.user_of(ctx.data.get("requester"))
    item = await ctx.db.get(InventoryItem, uuid.UUID(ctx.data["item_id"]))
    if user is None or item is None:
        return []
    thing = item.short_name.lower()
    if status == "declined":
        title, body = f"{ctx.actor_name} can't share the {thing}", "Not this time."
    else:
        title = f"{ctx.actor_name} said yes to {thing}"
        body = {
            "free": "On the house",
            "split": f"Added to the split ({signed(int(ctx.data.get('split_delta_cents') or 0))})",
            "chip_in": f"Chip in {money(int(ctx.data.get('chip_in_cents') or 0))}",
        }.get(str(resolution), "Yes")
    place = "pantry" if item.place == "pantry" else "fridge"
    rid = ctx.data["id"]
    return [Message(user, "answers", title, body, f"/kitchen/{place}?item={item.id}", f"ask-{rid}")]


async def _share(ctx: EventCtx) -> list[Message]:
    from app.services.bills import latest_snapshot

    deltas: dict[str, int] = ctx.data.get("share_deltas") or {}
    skip = set(ctx.data.get("notify_skip") or [])
    if ctx.bill is None or ctx.bill.state != "open":
        return []
    current = await latest_snapshot(ctx.db, ctx.bill.id)
    cause = await _share_cause(ctx)
    out = []
    for key, delta in deltas.items():
        if not key.startswith("m:") or key in skip or not delta:
            continue
        user = ctx.user_of(key[2:])
        if user is None:
            continue
        out.append(
            Message(
                user,
                "share",
                f"Your {ctx.store} share changed",
                "",
                f"/bills/{ctx.bill.id}",
                f"bill-{ctx.bill.id}",
                meta={"delta_cents": delta, "share_cents": current.get(key, 0), "cause": cause},
                group=f"share:{ctx.bill.id}",
            )
        )
    return out


async def _share_cause(ctx: EventCtx) -> str:
    changes = ctx.data.get("changes") or []
    if ctx.event.type != "allocation_changed" or len(changes) != 1:
        return ""
    [change] = changes
    item = await ctx.db.get(Item, uuid.UUID(change["item_id"]))
    who = change["participant"]
    if item is None or not str(who).startswith("m:"):
        return ""
    verb = "joined" if change.get("on") else "left"
    return f"{ctx.name(who[2:])} {verb} the {short_name(item.name).lower()}. "


def _share_body(meta: dict[str, Any]) -> str:
    return f"{meta.get('cause', '')}You're now at {money(meta['share_cents'])} ({signed(meta['delta_cents'])})."


async def _tab(ctx: EventCtx) -> list[Message]:
    if ctx.bill is None:
        return []
    shares: dict[str, int] = ctx.data.get("shares") or {}
    title = f"{ctx.name(ctx.bill.payer_member_id)} split a {ctx.store} run"
    out = []
    for mid in _member_keys(shares):
        user = ctx.user_of(mid)
        if user is not None and shares[f"m:{mid}"] > 0:  # user_of() already skips the sender
            body = f"Your share is {money(shares[f'm:{mid}'])}. Tick anything that's wrong."
            out.append(Message(user, "tab", title, body, f"/bills/{ctx.bill.id}", f"bill-{ctx.bill.id}"))
    return out


async def _pay(ctx: EventCtx) -> list[Message]:
    payment = await ctx.db.get(Payment, uuid.UUID(ctx.data["id"]))
    if payment is None or ctx.bill is None:
        return []
    url, tag = f"/bills/{ctx.bill.id}", f"pay-{payment.id}"
    payer = ctx.bill.payer_member_id
    if ctx.event.type == "payment_recorded":
        user = ctx.user_of(payer)
        who = ctx.name(payment.member_id) if payment.member_id else "A guest"
        return (
            [Message(user, "pay", f"{who} paid {money(payment.amount_cents)}", "Tap to confirm.", url, tag)]
            if user
            else []
        )
    user = ctx.user_of(payment.member_id)
    if user is None:
        return []
    if ctx.event.type == "payment_confirmed":
        title, body = f"{ctx.name(payer)} confirmed your {money(payment.amount_cents)}", f"{ctx.store} · all good."
    else:
        title = f"{ctx.name(payer)} didn't get your {money(payment.amount_cents)}"
        body = payment.reject_note or "Tap to check what happened."
    return [Message(user, "pay", title, body, url, tag)]


async def _remind(ctx: EventCtx) -> list[Message]:
    user = ctx.user_of(ctx.data.get("member_id"))
    if user is None or ctx.bill is None:
        return []
    activity = await ctx.db.scalar(
        select(ActivityEvent.payload).where(
            ActivityEvent.house_id == ctx.event.house_id, ActivityEvent.house_event_seq == ctx.event.seq
        )
    )
    owed = int((activity or {}).get("owed_cents") or 0)
    body = f"{ctx.store} · your share is {money(owed)}"
    return [
        Message(
            user, "remind", f"Quick nudge from {ctx.actor_name}", body, f"/bills/{ctx.bill.id}", f"remind-{ctx.bill.id}"
        )
    ]


async def _settled(ctx: EventCtx) -> list[Message]:
    if ctx.bill is None:
        return []
    title, body = {
        "bill_settled": (f"{ctx.store} is settled", "Nothing more to do on this one."),
        "bill_reopened": (f"{ctx.store} was reopened", "Check what changed."),
        "bill_voided": (f"{ctx.store} was cancelled", str(ctx.data.get("reason") or "It no longer counts.")),
    }.get(ctx.event.type, (f"{ctx.store} changed", ""))
    users = {ctx.user_of(m) for m in await _bill_members(ctx)} - {None}
    return [
        Message(u, "settled", title, body, f"/bills/{ctx.bill.id}", f"bill-{ctx.bill.id}")
        for u in users
        if u is not None
    ]


async def _joined(ctx: EventCtx) -> list[Message]:
    name = str(ctx.data.get("display_name") or ctx.actor_name)
    admins = [m.id for m in ctx.members.values() if m.role == "admin"]
    member_id = ctx.data.get("id") or ctx.event.aggregate_id
    return [
        Message(u, "joined", f"{name} joined {ctx.house.name}", "Say hi.", "/house", f"join-{member_id}")
        for u in {ctx.user_of(a) for a in admins} - {None}
        if u is not None
    ]


HANDLERS: dict[str, Callable[[EventCtx], Awaitable[list[Message]]]] = {
    "asks": _asks,
    "answers": _answers,
    "share": _share,
    "tab": _tab,
    "pay": _pay,
    "remind": _remind,
    "settled": _settled,
    "joined": _joined,
}


async def _event_ctx(db: AsyncSession, row: OutboxRow) -> EventCtx | None:
    event = await db.get(HouseEvent, (row.house_id, row.seq))
    house = await db.get(House, row.house_id)
    if event is None or house is None:
        return None
    members = {m.id: m for m in (await db.scalars(select(Member).where(Member.house_id == row.house_id))).all()}
    actor = members.get(event.actor_member_id) if event.actor_member_id else None
    bill_id = (event.data or {}).get("bill_id") or (event.aggregate_id if event.aggregate_type == "bill" else None)
    if bill_id is None:
        bill_id = await db.scalar(
            select(ActivityEvent.bill_id).where(
                ActivityEvent.house_id == row.house_id, ActivityEvent.house_event_seq == row.seq
            )
        )
    bill = await db.get(Bill, uuid.UUID(str(bill_id))) if bill_id else None
    return EventCtx(db, event, house, members, actor.user_id if actor else None, bill)


# ---- preferences and quiet hours --------------------------------------------------------------------


async def prefs_for(db: AsyncSession, user_id: uuid.UUID) -> dict[str, dict[str, bool]]:
    out = {ch: dict(v) for ch, v in DEFAULTS.items()}
    for p in (await db.scalars(select(NotificationPref).where(NotificationPref.user_id == user_id))).all():
        if p.type in out[p.channel]:
            out[p.channel][p.type] = p.enabled
    return out


def quiet_until(user: User, now: datetime) -> datetime | None:
    """End of the quiet window `now` falls in (user's time zone), else None. Windows may wrap midnight."""
    if user.quiet_start is None or user.quiet_end is None or user.quiet_start == user.quiet_end:
        return None
    tz = ZoneInfo(user.timezone)
    local = now.astimezone(tz)
    t, start, end = local.time(), user.quiet_start, user.quiet_end
    inside = (start <= t or t < end) if start > end else (start <= t < end)
    if not inside:
        return None
    day = local.date() if t < end else local.date() + timedelta(days=1)
    return datetime.combine(day, end, tzinfo=tz).astimezone(UTC)


# ---- fan-out ----------------------------------------------------------------------------------------


def _dedupe(event_id: str, kind: str, user_id: uuid.UUID, channel: str) -> str:
    return f"{event_id}:{kind}:{user_id}:{channel}"


async def create_notifications(
    db: AsyncSession,
    messages: Sequence[Message],
    *,
    event_id: str,
    house_id: uuid.UUID | None,
    seq: int | None,
    now: datetime,
    first_attempt: datetime | None = None,
    event_at: datetime | None = None,
) -> list[uuid.UUID]:
    """Insert one row per recipient × channel (in-app always; push/email per preferences)."""
    created: list[uuid.UUID] = []
    users = {u.id: u for u in (await db.scalars(select(User).where(User.id.in_({m.user_id for m in messages})))).all()}
    for msg in messages:
        user = users.get(msg.user_id)
        if user is None or user.deleted_at is not None:
            continue
        prefs = await prefs_for(db, user.id)
        channels = ["inapp"]
        if msg.type == "test" or prefs["push"].get(msg.type, False):
            channels.append("push")
        verified = user.email is not None and user.email_verified_at is not None  # EMAIL-01
        if msg.type != "test" and prefs["email"].get(msg.type, False) and verified and user.notify_email:
            channels.append("email")
        if msg.channels is not None:
            channels = [c for c in msg.channels if c != "email" or verified]
        for channel in channels:
            payload = {
                "title": msg.title,
                "body": msg.body,
                "url": msg.url,
                "tag": msg.tag,
                "event_id": event_id,
                "meta": msg.meta,
                "group": msg.group,
            }
            due = first_attempt or now
            if msg.group and channel != "inapp":  # the window opens when the change committed
                due = max(due, (event_at or now) + COALESCE)
            row_id = uuid7()
            # In-app rows are the inbox and appear at once, except scheduled ones (the 9:00 round-up),
            # which wait like the push and are re-checked when they go out.
            waits = channel != "inapp" or (first_attempt is not None and first_attempt > now)
            status = "pending" if waits else "sent"
            held = quiet_until(user, due) if channel != "inapp" else None
            if held is not None:
                status, due = "held", held
            inserted = await db.scalar(
                insert(Notification)
                .values(
                    id=row_id,
                    user_id=user.id,
                    house_id=house_id,
                    event_seq=seq,
                    type=msg.type,
                    channel=channel,
                    payload=payload,
                    dedupe_key=_dedupe(event_id, msg.type, user.id, channel),
                    status=status,
                    next_attempt_at=due if waits else None,
                    created_at=now,
                    sent_at=None if waits else now,
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
                .returning(Notification.id)
            )
            if inserted is None:
                continue  # a retried drain: already fanned out (PUSH-13)
            if msg.group:
                await _coalesce(db, user.id, channel, msg, row_id, now)
            created.append(row_id)
    return created


async def _coalesce(
    db: AsyncSession, user_id: uuid.UUID, channel: str, msg: Message, row_id: uuid.UUID, now: datetime
) -> None:
    """PUSH-11: share changes for one bill fold into the oldest waiting row (net change, latest share).
    An advisory lock per (user, channel, group) makes two drains running at once take turns here, so
    the second always sees the first's row."""
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"coalesce:{user_id}:{channel}:{msg.group}"},
    )
    window_start = now - COALESCE
    statuses = ("sent",) if channel == "inapp" else ("pending", "held")
    older = (
        await db.scalars(
            select(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.channel == channel,
                Notification.type == msg.type,
                Notification.status.in_(statuses),
                Notification.payload["group"].astext == msg.group,
                Notification.id != row_id,
                Notification.created_at >= window_start,
                *([Notification.read_at.is_(None)] if channel == "inapp" else []),
            )
            .order_by(Notification.created_at)
            .with_for_update(key_share=True)
        )
    ).first()
    if older is None:
        return
    meta = dict(older.payload.get("meta") or {})
    meta["delta_cents"] = int(meta.get("delta_cents", 0)) + int(msg.meta["delta_cents"])
    meta["share_cents"] = msg.meta["share_cents"]
    meta["changes"] = int(meta.get("changes", 1)) + 1
    meta["cause"] = ""  # several changes folded together: show only the net result
    older.payload = {**older.payload, "meta": meta}
    await db.execute(
        update(Notification)
        .where(Notification.id == row_id)
        .values(status="suppressed", last_error="coalesced", next_attempt_at=None)
    )


async def fan_out(db: AsyncSession, row: OutboxRow, now: datetime) -> int:
    ctx = await _event_ctx(db, row)
    handler = HANDLERS.get(row.kind)
    if ctx is None or handler is None:
        return 0
    messages = await handler(ctx)
    for m in messages:
        if m.type == "share":
            m.body = _share_body(m.meta)
    ids = await create_notifications(
        db,
        messages,
        event_id=f"{row.house_id}:{row.seq}",
        house_id=row.house_id,
        seq=row.seq,
        now=now,
        event_at=ctx.event.created_at,
    )
    return len(ids)


# ---- delivery ---------------------------------------------------------------------------------------


async def _badge(db: AsyncSession, user_id: uuid.UUID, now: datetime | None = None) -> int:
    """PUSH-16: asks still waiting on me (any house, still pending, not expired) + share changes I
    haven't seen (unread in-app rows). An ask answered by a co-owner drops off at once."""
    at = now or datetime.now(UTC)
    my_members = select(Member.id).where(Member.user_id == user_id, Member.status == "active")
    asks = await db.scalar(
        select(func.count(func.distinct(ShareRequest.id)))
        .select_from(ShareRequest)
        .join(InventoryOwner, InventoryOwner.inventory_item_id == ShareRequest.inventory_item_id)
        .where(
            ShareRequest.status == "pending",
            ShareRequest.expires_at > at,
            InventoryOwner.member_id.in_(my_members),
        )
    )
    shares = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.channel == "inapp",
            Notification.read_at.is_(None),
            Notification.type == "share",
            Notification.status == "sent",
        )
    )
    return int(asks or 0) + int(shares or 0)


def wire_payload(
    n: Notification, user: User, badge: int, grouped: int = 1, *, lock_screen: bool = True
) -> dict[str, Any]:
    """{title, body, url, tag, badge, event_id}, under 2 KB (PUSH-18); private hides details (PUSH-08)."""
    p = n.payload
    title, body = str(p["title"]), str(p["body"])
    if n.type == "share":
        body = _share_body(p["meta"])
    if grouped > 1:
        title, body = GROUPED.get(n.type, "{n} updates").format(n=grouped), "Tap to catch up."
    if lock_screen and user.private_lock_screen and n.type != "test":
        title, body = PRIVATE.get(n.type, "KitchenTab"), "Open KitchenTab to see it."
    out = {
        "title": _fit(title, 300),
        "body": _fit(body, 900),
        "url": p["url"] if grouped == 1 else "/",
        "tag": p["tag"] if grouped == 1 else f"group-{n.type}",
        "badge": badge,
        "event_id": p.get("event_id"),
    }
    return out


def _fit(text: str, max_bytes: int) -> str:
    """Trim to a UTF-8 byte budget (emoji are 4 bytes; the 2 KB limit is on bytes, PUSH-18)."""
    raw = text.encode()
    if len(raw) <= max_bytes:
        return text
    return raw[: max_bytes - 3].decode(errors="ignore") + "…"


def _local_day_start(user: User, now: datetime) -> datetime:
    tz = ZoneInfo(user.timezone)
    return datetime.combine(now.astimezone(tz).date(), time(0), tzinfo=tz).astimezone(UTC)


async def _finish(n: Notification, status: str, now: datetime, error: str | None = None) -> None:
    n.status, n.next_attempt_at, n.last_error = status, None, error
    if status == "sent":
        n.sent_at = now


async def _soon_still_due(db: AsyncSession, n: Notification) -> bool:
    """A round-up is only worth sending if some of its food is still in the kitchen."""
    ids = [uuid.UUID(i) for i in (n.payload.get("meta") or {}).get("item_ids", [])]
    if not ids:
        return True
    left = await db.scalar(
        select(func.count())
        .select_from(InventoryItem)
        .where(InventoryItem.id.in_(ids), InventoryItem.status == "in_stock")
    )
    return bool(left)


async def deliver(db: AsyncSession, deps: NotifyDeps, n: Notification, now: datetime) -> str:
    user = await db.get(User, n.user_id)
    if user is None or user.deleted_at is not None:
        await _finish(n, "suppressed", now, "no_user")
        return n.status
    if n.type == "soon" and not await _soon_still_due(db, n):
        await _finish(n, "suppressed", now, "no_longer_due")
        return n.status
    if n.channel == "inapp":  # a scheduled inbox row: it simply appears now
        await _finish(n, "sent", now)
        return n.status
    held = quiet_until(user, now) if n.type != "test" else None  # a test was asked for just now
    if held is not None:
        n.status, n.next_attempt_at = "held", held
        return n.status
    if n.type == "share" and abs(int(n.payload["meta"].get("delta_cents", 0))) < SHARE_THRESHOLD_CENTS:
        await _finish(n, "suppressed", now, "below_threshold")
        return n.status
    grouped = int(n.payload.get("grouped", 1))  # a retried morning summary stays a summary
    if n.status == "held":  # morning summary: one per type (PUSH-10)
        siblings = (
            await db.scalars(
                select(Notification)
                .where(
                    Notification.user_id == n.user_id,
                    Notification.channel == n.channel,
                    Notification.type == n.type,
                    Notification.status == "held",
                    Notification.id != n.id,
                    Notification.next_attempt_at <= now,
                )
                .with_for_update(skip_locked=True)
            )
        ).all()
        for s in siblings:
            await _finish(s, "suppressed", now, f"grouped:{n.id}")
        grouped += len(siblings)
        if grouped > 1:
            n.payload = {**n.payload, "grouped": grouped}
    if n.channel == "push":
        return await _deliver_push(db, deps, n, user, now, grouped)
    return await _deliver_email(deps, n, user, now, grouped)


async def _deliver_push(
    db: AsyncSession, deps: NotifyDeps, n: Notification, user: User, now: datetime, grouped: int
) -> str:
    if deps.push is None:
        await _finish(n, "suppressed", now, "push_disabled")
        return n.status
    sent_today = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user.id,
            Notification.channel == "push",
            Notification.status == "sent",
            Notification.type != "test",
            Notification.sent_at >= _local_day_start(user, now),
        )
    )
    if n.type != "test" and int(sent_today or 0) >= DAILY_PUSH_CAP:
        await _finish(n, "suppressed", now, "daily_cap")  # still in the inbox (PUSH-12)
        return n.status
    subs = (
        await db.scalars(
            select(PushSubscription)
            .where(PushSubscription.user_id == user.id, PushSubscription.stale_at.is_(None))
            .order_by(PushSubscription.created_at)
        )
    ).all()
    if not subs:
        await _finish(n, "suppressed", now, "no_devices")
        return n.status
    payload = wire_payload(n, user, await _badge(db, user.id, now), grouped)
    ttl = TTL_ASKS if n.type == "asks" else TTL_DEFAULT
    urgency: Urgency = "high" if n.type in HIGH_URGENCY else "normal"
    delivered, retryable = 0, 0
    gone: list[uuid.UUID] = []
    ok: list[uuid.UUID] = []
    failed: list[uuid.UUID] = []
    for sub in subs:
        try:
            result = await deps.push.send(
                SubscriptionInfo(sub.endpoint, sub.p256dh, sub.auth),
                payload,
                ttl=ttl,
                urgency=urgency,
                topic=_topic(payload["tag"]),
            )
        except ValueError:  # our payload, not the device: fail this one, never mark devices stale
            await _record_devices(deps, now, gone=gone, ok=ok, failed=failed)
            await _finish(n, "failed", now, "payload_too_large")
            return n.status
        except Exception:  # network error: count as a failure, keep the subscription
            log.warning("push send failed", extra={"notification": str(n.id)})
            result = PushResult(503)
        if result.gone:
            gone.append(sub.id)  # PUSH-05
        elif result.ok:
            ok.append(sub.id)
            delivered += 1
        else:
            failed.append(sub.id)
            retryable += 1
    await _record_devices(deps, now, gone=gone, ok=ok, failed=failed)
    if delivered:
        await _finish(n, "sent", now)
    elif retryable:
        _retry(n, now, "push_failed")
    else:
        await _finish(n, "suppressed", now, "no_devices")
    return n.status


async def _record_devices(
    deps: NotifyDeps, now: datetime, *, gone: list[uuid.UUID], ok: list[uuid.UUID], failed: list[uuid.UUID]
) -> None:
    """Device bookkeeping in its own short transaction, rows locked in id order (PUSH-19). Inside the
    drain's batch transaction these locks were held until the whole batch committed, so two drains
    delivering to the same person deadlocked, and the failed batch was sent again."""
    ids = sorted({*gone, *ok, *failed})
    if not ids:
        return
    async with deps.sessions() as db, db.begin():
        await db.execute(
            select(PushSubscription.id)
            .where(PushSubscription.id.in_(ids))
            .order_by(PushSubscription.id)
            .with_for_update()
        )
        if gone:
            await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
        if ok:
            await db.execute(
                update(PushSubscription).where(PushSubscription.id.in_(ok)).values(last_success_at=now, failure_count=0)
            )
        if failed:  # five failures in a row: the device is stale
            await db.execute(
                update(PushSubscription)
                .where(PushSubscription.id.in_(failed))
                .values(
                    failure_count=PushSubscription.failure_count + 1,
                    stale_at=case((PushSubscription.failure_count + 1 >= 5, now), else_=PushSubscription.stale_at),
                )
            )


def _topic(tag: str) -> str:
    """Web Push `Topic` header: URL-safe base64 alphabet, at most 32 characters."""
    clean = "".join(c if c.isalnum() or c in "-_" else "-" for c in tag)
    return clean[:32]


TYPE_LABELS = {
    "asks": "asks for your food",
    "answers": "answers to your asks",
    "share": "share changes",
    "tab": "new tabs",
    "pay": "payments",
    "remind": "reminders to pay",
    "soon": "food to use soon",
    "settled": "tabs settled or reopened",
    "joined": "people joining your house",
    "digest": "the weekly use-soon digest",
}


def unsubscribe_sig(secret: str, user_id: uuid.UUID, type_: str) -> str:
    return hmac.new(secret.encode(), f"unsub:{user_id}:{type_}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(deps: NotifyDeps, user_id: uuid.UUID, type_: str) -> str:
    sig = unsubscribe_sig(deps.unsubscribe_secret, user_id, type_)
    return f"{deps.web_origin}/api/v1/email/unsubscribe?u={user_id}&t={type_}&s={sig}"


def render_email(deps: NotifyDeps, n: Notification, user: User, grouped: int) -> Email:
    """Plain text plus an HTML part that follows the reader's light or dark mode (EMAIL-01, EMAIL-05)."""
    assert user.email is not None
    p = wire_payload(n, user, 0, grouped, lock_screen=False)
    link = f"{deps.web_origin}{p['url']}"
    unsub = unsubscribe_url(deps, user.id, n.type)
    why = f"You get this because email is on for {TYPE_LABELS.get(n.type, 'these updates')}."
    footer = [why, f"Stop these emails: {unsub}", "Change everything in Settings › Notifications."]
    if deps.postal_address:
        footer.append(deps.postal_address)
    text = "\n".join([p["body"], "", f"Open KitchenTab: {link}", "", "-- ", *footer])
    esc = html.escape
    footer_html = "<br>".join(esc(line) for line in footer[:1] + footer[2:])
    page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="color-scheme" content="light dark"><meta name="supported-color-schemes" content="light dark">
<style>
body{{margin:0;background:#ECEAE2;color:#121210;font:16px/1.5 -apple-system,Segoe UI,Roboto,sans-serif}}
.card{{max-width:480px;margin:24px auto;background:#FBFAF6;border:2px solid #121210;padding:24px}}
a.btn{{display:inline-block;background:#C6F432;color:#121210;padding:12px 20px;border:2px solid #121210;
font-weight:700;text-decoration:none}}
.foot{{max-width:480px;margin:0 auto 24px;color:#5A5850;font-size:13px}}
.foot a{{color:#5A5850}}
@media (prefers-color-scheme: dark){{
body{{background:#0D0D0B;color:#F1EFE7}}.card{{background:#1A1A17;border-color:#F1EFE7}}
.foot,.foot a{{color:#A9A699}}}}
</style></head><body>
<div class="card"><h1 style="font-size:20px;margin:0 0 8px">{esc(p["title"])}</h1>
<p style="margin:0 0 20px">{esc(p["body"])}</p><a class="btn" href="{esc(link)}">Open KitchenTab</a></div>
<div class="foot">{footer_html}<br><a href="{esc(unsub)}">Stop these emails</a></div>
</body></html>"""
    return Email(
        to=user.email,
        subject=p["title"],
        text=text,
        html=page,
        headers={
            "X-KitchenTab-Type": n.type,
            "List-Unsubscribe": f"<{unsub}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",  # RFC 8058
        },
    )


async def _deliver_email(deps: NotifyDeps, n: Notification, user: User, now: datetime, grouped: int) -> str:
    if deps.email is None or not user.email or user.email_verified_at is None:
        await _finish(n, "suppressed", now, "no_verified_email")
        return n.status
    try:
        await deps.email.send(render_email(deps, n, user, grouped))
    except Exception:
        log.warning("email send failed", extra={"notification": str(n.id)})
        _retry(n, now, "email_failed")
        return n.status
    await _finish(n, "sent", now)
    return n.status


async def unsubscribe(db: AsyncSession, secret: str, user_id: uuid.UUID, type_: str, sig: str) -> bool:
    """EMAIL-02: turns that type off for email only (push and the inbox are untouched)."""
    if type_ not in TYPE_LABELS or not hmac.compare_digest(sig, unsubscribe_sig(secret, user_id, type_)):
        return False
    user = await db.get(User, user_id, with_for_update={"key_share": True})
    if user is None:
        return False
    if type_ == "digest":
        user.notify_kitchen_digest = False
        return True
    row = await db.get(NotificationPref, (user_id, type_, "email"))
    if row is None:
        db.add(NotificationPref(user_id=user_id, type=type_, channel="email", enabled=False))
    else:
        row.enabled = False
    return True


def _retry(n: Notification, now: datetime, error: str) -> None:
    n.attempts += 1
    n.last_error = error
    if n.attempts > len(RETRIES):
        n.status, n.next_attempt_at = "failed", None
    else:
        n.status, n.next_attempt_at = "pending", now + RETRIES[n.attempts - 1]


# ---- drain ------------------------------------------------------------------------------------------


@dataclass
class DrainResult:
    outbox: int = 0
    notifications: int = 0
    sent: int = 0
    next_due: datetime | None = None
    out_of_time: bool = False


async def drain(deps: NotifyDeps, *, batch: int = 20, rounds: int = 50) -> DrainResult:
    """Fan out pending outbox rows, then deliver due push/email rows. Safe to run concurrently
    (FOR UPDATE SKIP LOCKED) and to repeat (dedupe keys)."""
    result = DrainResult()
    started = time_module.monotonic()
    for _ in range(rounds):
        if time_module.monotonic() - started > DRAIN_BUDGET_SECONDS:
            result.out_of_time = True
            break
        now = deps.clock.now()
        async with deps.sessions() as db, db.begin():
            rows = (
                await db.scalars(
                    select(OutboxRow)
                    .where(OutboxRow.status == "pending", OutboxRow.next_attempt_at <= now)
                    .order_by(OutboxRow.created_at, OutboxRow.seq)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for row in rows:
                try:
                    async with db.begin_nested():
                        await fan_out(db, row, now)
                    row.status = "done"
                except Exception as exc:
                    log.exception("outbox fan-out failed", extra={"seq": row.seq})
                    row.attempts += 1
                    row.last_error = type(exc).__name__
                    if row.attempts >= OUTBOX_RETRIES:
                        row.status = "failed"
                    else:
                        row.next_attempt_at = now + RETRIES[min(row.attempts, len(RETRIES)) - 1]
        result.outbox += len(rows)
        if len(rows) < batch:
            break
    for _ in range(rounds):
        if time_module.monotonic() - started > DRAIN_BUDGET_SECONDS:
            result.out_of_time = True
            break
        now = deps.clock.now()
        async with deps.sessions() as db, db.begin():
            due = (
                await db.scalars(
                    select(Notification)
                    .where(
                        Notification.status.in_(("pending", "held")),
                        Notification.next_attempt_at <= now,
                    )
                    .order_by(Notification.next_attempt_at, Notification.created_at)
                    .limit(batch)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            for n in due:
                if n.status in ("suppressed", "sent"):
                    continue  # grouped into a sibling earlier in this batch
                nid = n.id  # after a rolled-back savepoint `n` is expired: reading it would need IO
                try:
                    async with db.begin_nested():  # one bad row can't undo (and so re-send) the others
                        status = await deliver(db, deps, n, now)
                except Exception:
                    log.exception("notification delivery failed", extra={"notification": str(nid)})
                    await db.execute(
                        update(Notification)
                        .where(Notification.id == nid)
                        .values(status="failed", next_attempt_at=None, last_error="exception")
                    )
                    continue
                result.sent += status == "sent"
        result.notifications += len(due)
        if len(due) < batch:
            break
    async with deps.sessions() as db:
        result.next_due = await db.scalar(
            select(func.min(Notification.next_attempt_at)).where(Notification.status.in_(("pending", "held")))
        )
    if result.out_of_time and deps.schedule is not None:
        await deps.schedule(deps.clock.now())  # Vercel's 300 s limit: carry on in a fresh invocation
    elif result.next_due is not None and deps.schedule is not None:
        await deps.schedule(result.next_due)
    return result


# ---- direct sources (no house event) ------------------------------------------------------------------


async def push_test(deps: NotifyDeps, user_id: uuid.UUID) -> uuid.UUID | None:
    """`POST /push/test`: a "test" row for the caller's devices, delivered by the drain (PUSH-17)."""
    now = deps.clock.now()
    async with deps.sessions() as db, db.begin():
        msg = Message(
            user_id,
            "test",
            "It works!",
            "Notifications from KitchenTab will look like this.",
            "/settings/notifications",
            "test",
        )
        ids = await create_notifications(db, [msg], event_id=f"test:{uuid7()}", house_id=None, seq=None, now=now)
        # The caller asked for it right now: quiet hours do not apply to a test.
        await db.execute(
            update(Notification)
            .where(Notification.id.in_(ids), Notification.channel == "push")
            .values(status="pending", next_attempt_at=now)
        )
    return ids[0] if ids else None


async def use_soon_roundup(deps: NotifyDeps, *, at: time = time(9, 0), deliver_now: bool = False) -> int:
    """KITCH-15: one round-up per owner at 9:00 house time, once per item state change (a "Still
    good" snooze starts a new state). Scheduled by the daily cron for the next 9:00 in each house's
    time zone; every row (in-app too) waits until then and is re-checked when it goes out. Each house
    runs in its own savepoint so one bad house can't stop the others. `deliver_now` (test route
    only) sends at once instead of at 9:00, so browser journeys needn't move the shared clock."""
    now = deps.clock.now()
    count = 0
    async with deps.sessions() as db, db.begin():
        for house in (await db.scalars(select(House))).all():
            try:
                async with db.begin_nested():
                    count += await _soon_for_house(db, house, now, at, deliver_now=deliver_now)
            except Exception:
                log.exception("use-soon round-up failed for one house", extra={"house": str(house.id)})
    return count


def soon_key(item: InventoryItem, state: str) -> str:
    return f"{item.id}:{state}:{item.snoozed_until.isoformat() if item.snoozed_until else ''}"


async def _soon_for_house(db: AsyncSession, house: House, now: datetime, at: time, *, deliver_now: bool = False) -> int:
    from app.services.inventory import freshness

    tz = ZoneInfo(house.timezone)
    local = now.astimezone(tz)
    target_day = local.date() if local.time() < at else local.date() + timedelta(days=1)
    when = now if deliver_now else datetime.combine(target_day, at, tzinfo=tz).astimezone(UTC)
    items = (
        await db.scalars(
            select(InventoryItem).where(InventoryItem.house_id == house.id, InventoryItem.status == "in_stock")
        )
    ).all()
    states: dict[uuid.UUID, tuple[InventoryItem, str]] = {}
    for item in items:
        f = freshness(item, target_day)
        if f.probably_gone:
            states[item.id] = (item, "gone")
        elif f.use_soon:
            states[item.id] = (item, "soon")
    if not states:
        return 0
    owners: dict[uuid.UUID, list[uuid.UUID]] = {}
    for o in (await db.scalars(select(InventoryOwner).where(InventoryOwner.inventory_item_id.in_(list(states))))).all():
        owners.setdefault(o.member_id, []).append(o.inventory_item_id)
    members = {m.id: m for m in (await db.scalars(select(Member).where(Member.house_id == house.id))).all()}
    count = 0
    for member_id, item_ids in owners.items():
        member = members.get(member_id)
        if member is None or member.status != "active" or member.user_id is None:
            continue
        told = await _soon_keys(db, member.user_id, now)
        fresh = [i for i in item_ids if soon_key(*states[i]) not in told]
        if not fresh:
            continue
        msg = _soon_message(member, [states[i][0] for i in fresh], owners, members, target_day)
        msg.meta["keys"] = [soon_key(*states[i]) for i in fresh]
        msg.meta["item_ids"] = [str(i) for i in fresh]
        count += len(
            await create_notifications(
                db,
                [msg],
                event_id=f"soon:{house.id}:{target_day.isoformat()}",
                house_id=house.id,
                seq=None,
                now=now,
                first_attempt=when,
            )
        )
    return count


async def weekly_digest(deps: NotifyDeps, *, weekday: int = 6, at: time = time(10, 0)) -> int:
    """EMAIL-03: opted-in members get one email on Sunday 10:00 house time listing their use-soon
    food. The daily cron schedules it when the next Sunday 10:00 is less than a day and a half away."""
    now = deps.clock.now()
    count = 0
    async with deps.sessions() as db, db.begin():
        rows = (
            await db.execute(
                select(Member, House, User)
                .join(House, House.id == Member.house_id)
                .join(User, User.id == Member.user_id)
                .where(Member.status == "active", User.notify_kitchen_digest.is_(True), User.deleted_at.is_(None))
            )
        ).all()
        for member, house, user in rows:
            try:
                async with db.begin_nested():
                    count += await _digest_for(db, member, house, user, now, weekday, at)
            except Exception:
                log.exception("weekly digest failed for one member", extra={"house": str(house.id)})
    return count


async def _digest_for(
    db: AsyncSession, member: Member, house: House, user: User, now: datetime, weekday: int, at: time
) -> int:
    from app.services.inventory import freshness

    tz = ZoneInfo(house.timezone)
    local = now.astimezone(tz)
    days = (weekday - local.weekday()) % 7
    if days == 0 and local.time() >= at:
        days = 7
    day = local.date() + timedelta(days=days)
    when = datetime.combine(day, at, tzinfo=tz).astimezone(UTC)
    if when - now > timedelta(hours=36):
        return 0
    mine = (
        await db.scalars(
            select(InventoryItem)
            .join(InventoryOwner, InventoryOwner.inventory_item_id == InventoryItem.id)
            .where(
                InventoryItem.house_id == house.id,
                InventoryItem.status == "in_stock",
                InventoryOwner.member_id == member.id,
            )
        )
    ).all()
    soon = sorted((freshness(i, day).days_left, i.short_name) for i in mine if freshness(i, day).use_soon)
    if not soon:
        return 0
    lines = ", ".join(f"{name} ({'today' if left <= 0 else f'{left} d'})" for left, name in soon[:12])
    msg = Message(
        user.id,
        "digest",
        f"Use soon at {house.name}: {len(soon)} thing{'s' if len(soon) != 1 else ''}",
        lines,
        "/",
        f"digest-{day.isoformat()}",
        channels=("email",),
    )
    return len(
        await create_notifications(
            db,
            [msg],
            event_id=f"digest:{house.id}:{day.isoformat()}",
            house_id=house.id,
            seq=None,
            now=now,
            first_attempt=when,
        )
    )


async def _soon_keys(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> set[str]:
    rows = (
        await db.scalars(
            select(Notification.payload).where(
                Notification.user_id == user_id,
                Notification.type == "soon",
                Notification.channel == "inapp",
                Notification.created_at > now - timedelta(days=90),
            )
        )
    ).all()
    return {k for p in rows for k in (p.get("meta") or {}).get("keys", [])}


def _soon_message(
    me: Member,
    items: list[InventoryItem],
    owners: dict[uuid.UUID, list[uuid.UUID]],
    members: dict[uuid.UUID, Member],
    day: date,
) -> Message:
    from app.services.inventory import freshness

    assert me.user_id is not None
    tag, url = f"soon-{day.isoformat()}", "/"
    if len(items) > 1:
        names = ", ".join(i.short_name for i in items[:4]) + (" and more" if len(items) > 4 else "")
        return Message(me.user_id, "soon", f"{len(items)} things need eating soon", names, url, tag)
    [item] = items
    others = [members[m].display_name for m, ids in owners.items() if item.id in ids and m != me.id and m in members]
    whose = "Yours" if not others else "Yours and " + " and ".join(f"{n}'s" for n in others)
    left = freshness(item, day).days_left
    when = (
        "Still there? Check it."
        if left < 0
        else "Use it today."
        if left == 0
        else "About a day left."
        if left == 1
        else f"About {left} days left."
    )
    return Message(me.user_id, "soon", f"{item.short_name} needs eating", f"{whose}. {when}", url, tag)


async def inbox(
    db: AsyncSession, user_id: uuid.UUID, cursor: str | None, limit: int = 30, now: datetime | None = None
) -> dict[str, Any]:
    q = select(Notification).where(
        Notification.user_id == user_id, Notification.channel == "inapp", Notification.status == "sent"
    )
    if cursor:
        at_raw, _, id_raw = cursor.partition("|")
        try:
            at, cid = datetime.fromisoformat(at_raw), uuid.UUID(id_raw)
        except ValueError as exc:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "That page link is broken; start from the top") from exc
        q = q.where(or_(Notification.created_at < at, (Notification.created_at == at) & (Notification.id < cid)))
    rows = (await db.scalars(q.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit + 1))).all()
    page = rows[:limit]
    unread = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.user_id == user_id,
            Notification.channel == "inapp",
            Notification.status == "sent",
            Notification.read_at.is_(None),
        )
    )
    return {
        "items": [
            {
                "id": str(n.id),
                "type": n.type,
                "title": n.payload["title"],
                "body": _share_body(n.payload["meta"]) if n.type == "share" else n.payload["body"],
                "url": n.payload["url"],
                "house_id": str(n.house_id) if n.house_id else None,
                "created_at": n.created_at.isoformat(),
                "read": n.read_at is not None,
            }
            for n in page
        ],
        "unread": int(unread or 0),
        "badge": await _badge(db, user_id, now),
        "next_cursor": f"{page[-1].created_at.isoformat()}|{page[-1].id}" if page and len(rows) > limit else None,
    }
