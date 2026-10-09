"""Open-bill life: payments, settle, reopen, void, reminders, "changed" badges (PRD §5.4, §6).

Money rules (§6): owed[p] = share[p] - confirmed payments to the payer + confirmed refunds back.
Pending payments never reduce owed. Settling carries every non-zero owed amount into the house
ledger (`carryover`); reopening writes reversing entries (nothing financial is deleted). Voiding
drops the bill from balances and turns confirmed payments into `void_credit` entries.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.models import ActivityEvent, Bill, InventoryItem, LedgerEntry, Member, Payment, SeenMarker, ShareSnapshot
from app.policy import Action, require
from app.services import bill_math, kitchen
from app.services.bill_math import BillRows, gkey, mkey
from app.services.bills import _actor, _lock_bill, bill_ctx, bill_house_id, latest_snapshot
from app.services.house_write import Activity, Idempotency, WriteContext, house_write

REOPEN_WINDOW = timedelta(days=30)
REMIND_EVERY = timedelta(hours=48)
DUPLICATE_PAYMENT_WINDOW = timedelta(minutes=2)


def _pkey(p: Payment) -> str:
    return mkey(p.member_id) if p.member_id is not None else gkey(p.guest_id)  # type: ignore[arg-type]


async def payments_for(db: AsyncSession, bill_id: uuid.UUID) -> list[Payment]:
    return list(
        (await db.scalars(select(Payment).where(Payment.bill_id == bill_id).order_by(Payment.created_at))).all()
    )


def owed_map(rows: BillRows, payments: list[Payment]) -> dict[str, int]:
    """Per non-payer participant: share minus confirmed net payments. Negative = credit."""
    shares = bill_math.shares(rows).shares
    paid: dict[str, int] = defaultdict(int)
    for p in payments:
        if p.status != "confirmed":
            continue
        paid[_pkey(p)] += p.amount_cents if p.direction == "to_payer" else -p.amount_cents
    payer = mkey(rows.bill.payer_member_id)
    return {k: v - paid[k] for k, v in shares.items() if k != payer and (v != 0 or paid[k] != 0)}


def payment_view(p: Payment) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "participant": _pkey(p),
        "direction": p.direction,
        "amount_cents": p.amount_cents,
        "method": p.method,
        "note": p.note,
        "status": p.status,
        "reject_note": p.reject_note,
        "version": p.version,
        "created_at": p.created_at.isoformat(),
        "confirmed_at": p.confirmed_at.isoformat() if p.confirmed_at else None,
    }


# ---- payments ---------------------------------------------------------------------------------------


async def record_payment(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    body: dict[str, Any],
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-36: a member records their own payment (pending). FR-38: the payer records one on someone's
    behalf (cash), confirmed at once. Different keys within 2 minutes both count but warn (SYNC-10)."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        require(Action.BILL_READ, actor=me, bill=bill_ctx(bill))
        if bill.state != "open":
            raise Problem(
                409, ErrorCode.BILL_SETTLED if bill.state == "settled" else ErrorCode.BILL_VOIDED, "This tab isn't open"
            )
        is_payer = me.member_id == bill.payer_member_id
        participant = body.get("participant") or mkey(me.member_id)
        if participant == mkey(bill.payer_member_id):
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "The payer doesn't pay themselves")
        if not is_payer and participant != mkey(me.member_id):
            raise Problem(403, ErrorCode.FORBIDDEN, "You can only record your own payment")
        rows = await bill_math.load(w.session, bill)
        existing = await payments_for(w.session, bill_id)
        owed = owed_map(rows, existing).get(participant, 0)
        amount = int(body.get("amount_cents") or max(owed, 0))
        if amount <= 0:
            raise Problem(422, ErrorCode.VALIDATION_FAILED, "Nothing to pay on this tab")
        kind, _, raw = participant.partition(":")
        recent = next(
            (
                p
                for p in reversed(existing)
                if _pkey(p) == participant
                and p.direction == "to_payer"
                and p.status != "rejected"
                and now - p.created_at < DUPLICATE_PAYMENT_WINDOW
            ),
            None,
        )
        payment = Payment(
            id=uuid7(),
            bill_id=bill_id,
            member_id=uuid.UUID(raw) if kind == "m" else None,
            guest_id=uuid.UUID(raw) if kind == "g" else None,
            to_member_id=bill.payer_member_id,
            direction="to_payer",
            amount_cents=amount,
            method=body.get("method") or "other",
            note=(body.get("note") or None),
            status="confirmed" if is_payer else "pending_confirmation",
            recorded_by=user_id,
            confirmed_by=user_id if is_payer else None,
            confirmed_at=now if is_payer else None,
            created_at=now,
        )
        w.session.add(payment)
        await w.session.flush()
        w.emit(
            "payment_recorded",
            aggregate=("payment", payment.id, 1),
            data=payment_view(payment),
            activity=Activity(
                "payment_recorded",
                bill_id=bill_id,
                actor_user_id=user_id,
                payload={"participant": participant, "amount_cents": amount, "status": payment.status},
            ),
            notify=[] if is_payer else ["pay"],
        )
        out = {**payment_view(payment)}
        if recent is not None:
            out["warning"] = {
                "code": "POSSIBLE_DUPLICATE",
                "amount_cents": recent.amount_cents,
                "recorded_at": recent.created_at.isoformat(),
            }
        w.respond(201, out)
    return out


async def answer_payment(
    writes: WriteContext,
    payment_id: uuid.UUID,
    user_id: uuid.UUID,
    confirm: bool,
    note: str | None,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-37: the payer confirms (owed drops) or rejects (member notified)."""
    async with writes.sessions() as db:
        bill_id = await db.scalar(select(Payment.bill_id).where(Payment.id == payment_id))
        if bill_id is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Payment not found")
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        if me.member_id != bill.payer_member_id:
            raise Problem(403, ErrorCode.FORBIDDEN, "Only the payer confirms payments")
        if bill.state != "open":
            raise Problem(
                409, ErrorCode.BILL_SETTLED if bill.state == "settled" else ErrorCode.BILL_VOIDED, "This tab isn't open"
            )
        p = (
            await w.session.scalars(select(Payment).where(Payment.id == payment_id).with_for_update(key_share=True))
        ).one()
        if p.status != "pending_confirmation":
            raise Problem(
                409, ErrorCode.ALREADY_ANSWERED, "This payment was already answered", extra={"payment": payment_view(p)}
            )
        p.status = "confirmed" if confirm else "rejected"
        p.confirmed_by = user_id
        p.confirmed_at = now
        p.reject_note = None if confirm else (note or None)
        p.version += 1
        event = "payment_confirmed" if confirm else "payment_rejected"
        w.emit(
            event,
            aggregate=("payment", p.id, p.version),
            data=payment_view(p),
            activity=Activity(
                event,
                bill_id=bill_id,
                actor_user_id=user_id,
                payload={"participant": _pkey(p), "amount_cents": p.amount_cents},
            ),
            notify=["pay"],
        )
        out = payment_view(p)
        w.respond(200, out)
    return out


# ---- settle / reopen / void ----------------------------------------------------------------------------


def _ledger_from_owed(
    house_id: uuid.UUID, bill: Bill, owed: dict[str, int], now: datetime, *, reverse: bool = False
) -> list[LedgerEntry]:
    entries = []
    for key, amount in owed.items():
        if amount == 0 or not key.startswith("m:"):
            continue  # guests have no house balance (§6 edge rules)
        member = uuid.UUID(key[2:])
        debtor, creditor = (member, bill.payer_member_id) if amount > 0 else (bill.payer_member_id, member)
        if reverse:
            debtor, creditor = creditor, debtor
        entries.append(
            LedgerEntry(
                id=uuid7(),
                house_id=house_id,
                from_member_id=debtor,
                to_member_id=creditor,
                amount_cents=abs(amount),
                source="carryover",
                bill_id=bill.id,
                created_at=now,
            )
        )
    return entries


async def _names(db: AsyncSession, keys: list[str]) -> dict[str, str]:
    ids = [uuid.UUID(k[2:]) for k in keys if k.startswith("m:")]
    rows = (await db.execute(select(Member.id, Member.display_name).where(Member.id.in_(ids)))).all() if ids else []
    return {mkey(i): n for i, n in rows}


async def settle(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    confirm_outstanding: bool,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-39: payer stamps it settled; with money still owed only after the confirm dialog, and the
    remainder moves to house balances as carryover ledger entries."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)  # FOR UPDATE: waits for in-flight ticks (SYNC-06)
        if bill.state != "open":
            raise Problem(
                409, ErrorCode.BILL_SETTLED if bill.state == "settled" else ErrorCode.CONFLICT, "This tab isn't open"
            )
        if me.member_id != bill.payer_member_id:
            raise Problem(403, ErrorCode.FORBIDDEN, "Only the payer can settle this tab")
        rows = await bill_math.load(w.session, bill)
        owed = owed_map(rows, await payments_for(w.session, bill_id))
        outstanding = {k: v for k, v in owed.items() if v != 0}
        if outstanding and not confirm_outstanding:
            names = await _names(w.session, list(outstanding))
            raise Problem(
                409,
                ErrorCode.CONFLICT,
                "Money is still owed",
                extra={
                    "outstanding": [
                        {"participant": k, "name": names.get(k), "owed_cents": v} for k, v in outstanding.items()
                    ]
                },
            )
        entries = _ledger_from_owed(house_id, bill, outstanding, now)
        w.session.add_all(entries)
        bill.state = "settled"
        bill.settled_at = now
        bill.settled_by = me.member_id
        bill.version += 1
        w.emit(
            "bill_settled",
            aggregate=("bill", bill.id, bill.version),
            data={
                "carryover": [
                    {"from": str(e.from_member_id), "to": str(e.to_member_id), "amount_cents": e.amount_cents}
                    for e in entries
                ]
            },
            activity=Activity(
                "bill_settled", bill_id=bill.id, actor_user_id=user_id, payload={"outstanding": outstanding}
            ),
            notify=["settled"],
        )
        out = {"id": str(bill.id), "state": "settled", "version": bill.version, "carried_over": len(entries)}
        w.respond(200, out)
    return out


async def reopen(
    writes: WriteContext, bill_id: uuid.UUID, user_id: uuid.UUID, now: datetime, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    """FR-40: payer reopens within 30 days; the carryover is reversed, never deleted."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        if me.member_id != bill.payer_member_id:
            raise Problem(403, ErrorCode.FORBIDDEN, "Only the payer can reopen this tab")
        if bill.state != "settled" or bill.settled_at is None:
            raise Problem(409, ErrorCode.CONFLICT, "Only settled tabs can be reopened")
        if now - bill.settled_at > REOPEN_WINDOW:
            raise Problem(409, ErrorCode.CONFLICT, "Tabs can be reopened for 30 days after settling")
        carry = (
            await w.session.scalars(
                select(LedgerEntry).where(LedgerEntry.bill_id == bill.id, LedgerEntry.source == "carryover")
            )
        ).all()
        net: dict[tuple[uuid.UUID, uuid.UUID], int] = defaultdict(int)
        for e in carry:
            net[(e.from_member_id, e.to_member_id)] += e.amount_cents
            net[(e.to_member_id, e.from_member_id)] -= e.amount_cents
        for (debtor, creditor), amount in net.items():
            if amount > 0:  # reverse what is still carried
                w.session.add(
                    LedgerEntry(
                        id=uuid7(),
                        house_id=house_id,
                        from_member_id=creditor,
                        to_member_id=debtor,
                        amount_cents=amount,
                        source="carryover",
                        bill_id=bill.id,
                        created_at=now,
                    )
                )
        bill.state = "open"
        bill.settled_at = None
        bill.settled_by = None
        bill.version += 1
        w.emit(
            "bill_reopened",
            aggregate=("bill", bill.id, bill.version),
            activity=Activity("bill_reopened", bill_id=bill.id, actor_user_id=user_id),
            notify=["settled"],
        )
        out = {"id": str(bill.id), "state": "open", "version": bill.version}
        w.respond(200, out)
    return out


async def void(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    reason: str,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-42: open bills are voided (not deleted) with a reason; they leave balances, confirmed
    payments become credits to the people who paid, and the kitchen items leave."""
    reason = " ".join(reason.split())
    if not 3 <= len(reason) <= 200:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Say why in 3 to 200 characters")
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        if me.member_id != bill.payer_member_id:
            raise Problem(403, ErrorCode.FORBIDDEN, "Only the payer can void this tab")
        if bill.state != "open":
            raise Problem(409, ErrorCode.CONFLICT, "Only open tabs can be voided")
        for p in await payments_for(w.session, bill_id):
            if p.status == "confirmed" and p.direction == "to_payer" and p.member_id is not None:
                w.session.add(
                    LedgerEntry(
                        id=uuid7(),
                        house_id=house_id,
                        from_member_id=bill.payer_member_id,
                        to_member_id=p.member_id,
                        amount_cents=p.amount_cents,
                        source="void_credit",
                        bill_id=bill.id,
                        created_at=now,
                    )
                )
        items = (
            await w.session.scalars(
                select(InventoryItem).where(InventoryItem.bill_id == bill.id, InventoryItem.status == "in_stock")
            )
        ).all()
        for inv in items:
            inv.status = "removed"
            inv.updated_at = now
            inv.version += 1
            await kitchen._close_access(w.session, inv.id, now)
        bill.state = "voided"
        bill.void_reason = reason
        bill.voided_at = now
        bill.version += 1
        w.emit(
            "bill_voided",
            aggregate=("bill", bill.id, bill.version),
            data={"reason": reason},
            activity=Activity("bill_voided", bill_id=bill.id, actor_user_id=user_id, payload={"reason": reason}),
            notify=["settled"],
        )
        out = {"id": str(bill.id), "state": "voided", "version": bill.version}
        w.respond(200, out)
    return out


async def remind(
    writes: WriteContext,
    bill_id: uuid.UUID,
    user_id: uuid.UUID,
    member_id: uuid.UUID,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """UC-12 / PAY-07: payer nudges an unpaid member, at most once per member per bill per 48 h."""
    async with writes.sessions() as db:
        house_id = await bill_house_id(db, bill_id)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await _actor(w, user_id)
        bill = await _lock_bill(w, bill_id)
        if me.member_id != bill.payer_member_id:
            raise Problem(403, ErrorCode.FORBIDDEN, "Only the payer sends reminders")
        if bill.state != "open":
            raise Problem(409, ErrorCode.CONFLICT, "This tab isn't open")
        last = await w.session.scalar(
            select(func.max(ActivityEvent.created_at)).where(
                ActivityEvent.bill_id == bill_id,
                ActivityEvent.type == "reminder_sent",
                ActivityEvent.payload["member_id"].astext == str(member_id),
            )
        )
        if last is not None and now - last < REMIND_EVERY:
            retry = int((last + REMIND_EVERY - now).total_seconds())
            raise Problem(
                429,
                ErrorCode.RATE_LIMITED,
                "You already nudged them recently",
                headers={"Retry-After": str(retry)},
                extra={"retry_after": retry},
            )
        rows = await bill_math.load(w.session, bill)
        owed = owed_map(rows, await payments_for(w.session, bill_id)).get(mkey(member_id), 0)
        if owed <= 0:
            raise Problem(409, ErrorCode.CONFLICT, "They don't owe anything on this tab")
        w.emit(
            "reminder_sent",
            aggregate=("bill", bill.id, bill.version),
            data={"member_id": str(member_id)},
            activity=Activity(
                "reminder_sent",
                bill_id=bill.id,
                actor_user_id=user_id,
                payload={"member_id": str(member_id), "owed_cents": owed},
            ),
            notify=["remind"],
        )
        out = {"member_id": str(member_id), "owed_cents": owed}
        w.respond(200, out)
    return out


# ---- "changed since you last looked" (FR-35) ---------------------------------------------------------


async def mark_seen(db: AsyncSession, user_id: uuid.UUID, bill_id: uuid.UUID, version: int, now: datetime) -> None:
    """Set-state "I've looked at this bill now". Ticks don't bump the bill version (SYNC-04), so the
    badge compares against the share as of the moment the user last looked, not a version."""
    marker = await db.get(SeenMarker, (user_id, bill_id), with_for_update={"key_share": True})
    if marker is None:
        db.add(SeenMarker(user_id=user_id, bill_id=bill_id, last_seen_version=version, updated_at=now))
    else:
        marker.last_seen_version = max(version, marker.last_seen_version)
        marker.updated_at = max(now, marker.updated_at)


async def changed_since_seen(
    db: AsyncSession, user_id: uuid.UUID, member_id: uuid.UUID, bill: Bill
) -> dict[str, Any] | None:
    """FR-35: my share now minus my share when I last looked; None when unchanged or never looked."""
    if bill.state == "draft":
        return None
    marker = await db.get(SeenMarker, (user_id, bill.id))
    if marker is None:
        return None  # first view: nothing to compare
    snaps = (
        await db.execute(
            select(ShareSnapshot.computed_at, ShareSnapshot.share_cents)
            .where(ShareSnapshot.bill_id == bill.id, ShareSnapshot.member_id == member_id)
            .order_by(ShareSnapshot.computed_at)
        )
    ).all()
    if not snaps:
        return None
    current = snaps[-1][1]
    before = next((cents for at, cents in reversed(snaps) if at <= marker.updated_at), snaps[0][1])
    delta = current - before
    return {"delta_cents": delta, "since": marker.updated_at.isoformat()} if delta else None


__all__ = [
    "answer_payment",
    "changed_since_seen",
    "latest_snapshot",
    "mark_seen",
    "owed_map",
    "payment_view",
    "record_payment",
    "remind",
    "reopen",
    "settle",
    "void",
]
