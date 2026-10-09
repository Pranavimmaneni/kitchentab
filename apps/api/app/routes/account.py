"""Settings: sign-in methods, payment handles, export my data, delete account (PRD §7, §13, §14A Settings).

* Attaching an email or phone needs a verified code; a method tied to another user is refused,
  never merged (AUTH-11). The last sign-in method can't be removed (AUTH-12).
* Deleting an account anonymizes the person's members to "Former roommate" in every house and keeps
  every amount for the others (AUTH-14); sessions, identities, push subscriptions, payment handles
  and personal fields are erased (SEC-13). An admin with other active members must hand over first.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select

from app.adapters.email import Email, email_sender
from app.adapters.sms import sms_verifier
from app.deps import Actor, clear_session_cookie, ctx, rate_limit, require_actor
from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.models import (
    ActivityEvent,
    AuthIdentity,
    Bill,
    House,
    Member,
    NotificationPref,
    Payment,
    PaymentHandle,
    PushSubscription,
    Session,
    User,
)
from app.security import mask_destination, new_otp_code, normalize_email, normalize_us_phone
from app.services import auth as auth_svc
from app.services.house_write import Activity, house_write
from app.services.houses import member_view
from app.services.inventory import member_left

router = APIRouter(tags=["account"])
FORMER = "Former roommate"


# ---- sign-in methods ---------------------------------------------------------------------------


@router.get("/me/identities")
async def identities(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> list[dict[str, Any]]:
    async with ctx(request).sessions() as db:
        rows = (
            await db.scalars(
                select(AuthIdentity).where(AuthIdentity.user_id == actor.user.id).order_by(AuthIdentity.created_at)
            )
        ).all()
    return [
        {
            "id": str(i.id),
            "provider": i.provider,
            "label": mask_destination(i.provider, i.provider_subject) if i.provider in ("email", "phone") else "Google",
        }
        for i in rows
    ]


class AttachStart(BaseModel):
    channel: Literal["email", "phone"]
    destination: str = Field(min_length=3, max_length=254)


def _normalize(channel: str, raw: str) -> str:
    value = normalize_email(raw) if channel == "email" else normalize_us_phone(raw)
    if value is None:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "That doesn't look right.")
    return value


async def _taken_by_other(request: Request, provider: str, subject: str, user_id: uuid.UUID) -> bool:
    async with ctx(request).sessions() as db:
        owner = await db.scalar(
            select(AuthIdentity.user_id).where(
                AuthIdentity.provider == provider, AuthIdentity.provider_subject == subject
            )
        )
        if owner is None and provider == "email":
            owner = await db.scalar(select(User.id).where(User.email == subject, User.deleted_at.is_(None)))
        if owner is None and provider == "phone":
            owner = await db.scalar(select(User.id).where(User.phone_e164 == subject, User.deleted_at.is_(None)))
    return owner is not None and owner != user_id


@router.post("/me/identities/start")
async def attach_start(
    body: AttachStart, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    c = ctx(request)
    destination = _normalize(body.channel, body.destination)
    if await _taken_by_other(request, body.channel, destination, actor.user.id):
        raise Problem(
            409, ErrorCode.IDENTITY_TAKEN, "This is linked to another account", "Sign in with that one instead."
        )
    pepper = c.settings.token_hash_pepper.get_secret_value()
    if body.channel == "email":
        await rate_limit(request, "otp_email", destination, limit=5, window=timedelta(hours=1))
        code = new_otp_code()
        async with c.sessions() as db, db.begin():
            await auth_svc.store_code(db, "email", destination, code, pepper, c.clock.now())
        await email_sender(c.settings, c.sessions, c.clock).send(
            Email(
                to=destination,
                subject=f"{code} is your KitchenTab code",
                text=f"Your KitchenTab code is {code}. It works for 10 minutes.",
            )
        )
    else:
        verifier = sms_verifier(c.settings, c.sessions, c.clock)
        if verifier is None:
            raise Problem(404, ErrorCode.METHOD_DISABLED, "Phone codes aren't available yet.")
        await rate_limit(request, "otp_phone", destination, limit=3, window=timedelta(hours=1))
        await verifier.start(destination)
        async with c.sessions() as db, db.begin():
            await auth_svc.store_code(db, "phone", destination, None, pepper, c.clock.now())
    return {"sent": True, "masked": mask_destination(body.channel, destination)}


class AttachVerify(AttachStart):
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/me/identities/verify", status_code=201)
async def attach_verify(
    body: AttachVerify, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    c = ctx(request)
    destination = _normalize(body.channel, body.destination)
    pepper = c.settings.token_hash_pepper.get_secret_value()
    now = c.clock.now()
    async with c.sessions() as db, db.begin():
        row = await auth_svc.latest_code(db, body.channel, destination, pepper)
        problem = auth_svc.code_problem(row, now)
        if problem is not None:
            raise problem
        assert row is not None
        if body.channel == "phone":
            verifier = sms_verifier(c.settings, c.sessions, c.clock)
            ok = verifier is not None and await verifier.check(destination, body.code)
        else:
            ok = auth_svc.code_matches(row, body.code, pepper)
        if ok:
            row.consumed_at = now
        else:
            row.attempts += 1
    if not ok:
        raise Problem(400, ErrorCode.CODE_MISMATCH, "That code didn't match.")
    if await _taken_by_other(request, body.channel, destination, actor.user.id):
        raise Problem(409, ErrorCode.IDENTITY_TAKEN, "This is linked to another account")
    async with c.sessions() as db, db.begin():
        user = await db.get(User, actor.user.id, with_for_update={"key_share": True})
        assert user is not None
        exists = await db.scalar(
            select(AuthIdentity.id).where(
                AuthIdentity.provider == body.channel, AuthIdentity.provider_subject == destination
            )
        )
        if exists is None:
            db.add(
                AuthIdentity(
                    id=uuid7(), user_id=user.id, provider=body.channel, provider_subject=destination, created_at=now
                )
            )
        if body.channel == "email":
            user.email, user.email_verified_at = destination, now
        else:
            user.phone_e164, user.phone_verified_at = destination, now
    return {"provider": body.channel, "label": mask_destination(body.channel, destination)}


@router.delete("/me/identities/{identity_id}")
async def remove_identity(
    identity_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    async with ctx(request).sessions() as db, db.begin():
        mine = (
            await db.scalars(
                select(AuthIdentity).where(AuthIdentity.user_id == actor.user.id).with_for_update(key_share=True)
            )
        ).all()
        target = next((i for i in mine if i.id == identity_id), None)
        if target is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Sign-in method not found")
        if len(mine) == 1:
            raise Problem(422, ErrorCode.LAST_SIGN_IN_METHOD, "Add another way to sign in before removing this one")
        await db.delete(target)
        user = await db.get(User, actor.user.id)
        assert user is not None
        if target.provider == "email" and not any(
            i.provider in ("email", "google") and i.id != target.id for i in mine
        ):
            user.email, user.email_verified_at = None, None
        if target.provider == "phone":
            user.phone_e164, user.phone_verified_at = None, None
    return {"removed": str(identity_id)}


# ---- payment handles (PRD §8 deep links) ---------------------------------------------------------


class Handles(BaseModel):
    venmo: str | None = Field(default=None, max_length=60, pattern=r"^@?[A-Za-z0-9_-]{2,59}$")
    cashapp: str | None = Field(default=None, max_length=60, pattern=r"^\$?[A-Za-z][A-Za-z0-9_-]{0,58}$")
    paypal: str | None = Field(default=None, max_length=60, pattern=r"^[A-Za-z0-9_.-]{2,60}$")
    zelle: str | None = Field(default=None, max_length=120)


@router.get("/me/payment-handles")
async def get_handles(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> dict[str, str | None]:
    async with ctx(request).sessions() as db:
        rows = (await db.scalars(select(PaymentHandle).where(PaymentHandle.user_id == actor.user.id))).all()
    out: dict[str, str | None] = {"venmo": None, "cashapp": None, "paypal": None, "zelle": None}
    out.update({h.method: h.handle for h in rows})
    return out


@router.put("/me/payment-handles")
async def put_handles(
    body: Handles, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, str | None]:
    """Set-state: the whole set of handles; empty removes one."""
    c = ctx(request)
    now = c.clock.now()
    values = {
        "venmo": (body.venmo or "").lstrip("@") or None,
        "cashapp": (body.cashapp or "").lstrip("$") or None,
        "paypal": body.paypal or None,
        "zelle": (body.zelle or "").strip() or None,
    }
    async with c.sessions() as db, db.begin():
        await db.execute(delete(PaymentHandle).where(PaymentHandle.user_id == actor.user.id))
        for method, handle in values.items():
            if handle:
                db.add(
                    PaymentHandle(
                        id=uuid7(), user_id=actor.user.id, method=method, handle=handle, created_at=now, updated_at=now
                    )
                )
    return values


# ---- export my data / delete account --------------------------------------------------------------


@router.get("/me/export")
async def export_my_data(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Response:
    """DATA-04: everything about me as JSON."""
    import json

    async with ctx(request).sessions() as db:
        user = actor.user
        members = (await db.scalars(select(Member).where(Member.user_id == user.id))).all()
        houses = {
            h.id: h for h in (await db.scalars(select(House).where(House.id.in_([m.house_id for m in members])))).all()
        }
        member_ids = [m.id for m in members]
        payments = (
            (await db.scalars(select(Payment).where(Payment.member_id.in_(member_ids)))).all() if member_ids else []
        )
        bills = (await db.scalars(select(Bill).where(Bill.payer_member_id.in_(member_ids)))).all() if member_ids else []
        identities = (await db.scalars(select(AuthIdentity).where(AuthIdentity.user_id == user.id))).all()
        handles = (await db.scalars(select(PaymentHandle).where(PaymentHandle.user_id == user.id))).all()
        prefs = (await db.scalars(select(NotificationPref).where(NotificationPref.user_id == user.id))).all()
        activity = (
            await db.scalars(
                select(ActivityEvent).where(ActivityEvent.actor_user_id == user.id).order_by(ActivityEvent.created_at)
            )
        ).all()
    data = {
        "user": {
            "id": str(user.id),
            "display_name": user.display_name,
            "email": user.email,
            "phone": user.phone_e164,
            "theme": user.theme,
            "timezone": user.timezone,
            "created_at": user.created_at.isoformat(),
        },
        "sign_in_methods": [{"provider": i.provider, "subject": i.provider_subject} for i in identities],
        "payment_handles": {h.method: h.handle for h in handles},
        "notification_prefs": [{"type": p.type, "channel": p.channel, "enabled": p.enabled} for p in prefs],
        "houses": [{"house": houses[m.house_id].name, **member_view(m)} for m in members if m.house_id in houses],
        "bills_paid_for": [
            {
                "id": str(b.id),
                "store": b.store_name,
                "date": b.purchase_date.isoformat(),
                "total_cents": b.total_cents,
                "state": b.state,
            }
            for b in bills
        ],
        "payments": [
            {
                "bill_id": str(p.bill_id),
                "amount_cents": p.amount_cents,
                "status": p.status,
                "method": p.method,
                "created_at": p.created_at.isoformat(),
            }
            for p in payments
        ],
        "activity": [
            {"type": a.type, "bill_id": str(a.bill_id) if a.bill_id else None, "created_at": a.created_at.isoformat()}
            for a in activity
        ],
    }
    return Response(
        json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="kitchentab-my-data.json"'},
    )


class DeleteAccount(BaseModel):
    confirm: str


@router.delete("/me")
async def delete_account(
    body: DeleteAccount, request: Request, response: Response, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    if body.confirm != "DELETE":
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Type DELETE to confirm")
    c = ctx(request)
    now = c.clock.now()
    async with c.sessions() as db:
        members = (await db.scalars(select(Member).where(Member.user_id == actor.user.id))).all()
        for m in members:
            if m.role == "admin" and m.status == "active":
                others = await db.scalar(
                    select(func.count())
                    .select_from(Member)
                    .where(Member.house_id == m.house_id, Member.status == "active", Member.id != m.id)
                )
                if others:
                    raise Problem(409, ErrorCode.CONFLICT, "Make someone else admin of your house first")
    for m in members:  # one house write (and event) per house, house lock last
        async with house_write(c.writes, m.house_id, actor_member_id=m.id) as w:
            row = await w.session.get(Member, m.id, with_for_update={"key_share": True})
            assert row is not None
            row.display_name = FORMER
            row.anonymized_at = now
            row.user_id = None
            row.status = "left" if row.status in ("active", "placeholder") else row.status
            row.role = "member"
            row.version += 1
            await member_left(w.session, row.id)
            w.emit(
                "member_anonymized",
                aggregate=("member", row.id, row.version),
                data=member_view(row),
                activity=Activity("member_anonymized", payload={}),
            )
    async with c.sessions() as db, db.begin():
        uid = actor.user.id
        for model in (Session, AuthIdentity, PushSubscription, PaymentHandle, NotificationPref):
            await db.execute(delete(model).where(model.user_id == uid))
        user = await db.get(User, uid, with_for_update={"key_share": True})
        assert user is not None
        user.display_name, user.email, user.phone_e164, user.avatar_url = FORMER, None, None, None
        user.email_verified_at = user.phone_verified_at = None
        user.deleted_at = now
    clear_session_cookie(response, c.settings)
    return {"deleted": True}
