"""Houses, members, invites and claims (PRD §5.1, §7; FR-02..FR-09).

Every write goes through `house_write` (one transaction, house lock last, event + activity +
outbox). Authorization is `app.policy`; this module enforces business rules (limits, duplicates,
state machine for claims).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.links import invite_token
from app.models import Bill, House, Member, User
from app.models.enums import MAX_MEMBERS_PER_HOUSE
from app.policy import Action, MemberCtx, TargetMember, require
from app.services.house_write import Activity, HouseWrite, Idempotency, WriteContext, house_write

MEMBER_COLORS = ["#C6F432", "#6A4CFF", "#FF8A65", "#7FD3F7", "#F7D046", "#FF6FB5", "#4FD1A5", "#B0A6FF"]
COUNTED_STATUSES = ("placeholder", "active")


def clean_name(raw: str, *, max_len: int) -> str:
    name = " ".join(raw.split())
    if not 1 <= len(name) <= max_len:
        raise Problem(422, ErrorCode.VALIDATION_FAILED, f"Names are 1 to {max_len} characters")
    return name


async def membership(db: AsyncSession, house_id: uuid.UUID, user_id: uuid.UUID) -> MemberCtx | None:
    m = (await db.scalars(select(Member).where(Member.house_id == house_id, Member.user_id == user_id))).first()
    if m is None:
        return None
    return MemberCtx(m.id, user_id, m.role, m.status)


def target_of(m: Member) -> TargetMember:
    return TargetMember(m.id, m.user_id, m.role, m.status)


async def _member_for_update(db: AsyncSession, house_id: uuid.UUID, member_id: uuid.UUID) -> Member:
    m = (
        await db.scalars(select(Member).where(Member.id == member_id, Member.house_id == house_id).with_for_update())
    ).first()
    if m is None:
        raise Problem(404, ErrorCode.NOT_FOUND, "That person isn't in the house")
    return m


async def _ensure_name_free(
    db: AsyncSession, house_id: uuid.UUID, name: str, *, except_id: uuid.UUID | None = None
) -> None:
    q = select(Member.id).where(
        Member.house_id == house_id,
        func.lower(Member.display_name) == name.lower(),
        Member.status != "removed",
        Member.anonymized_at.is_(None),
    )
    if except_id is not None:
        q = q.where(Member.id != except_id)
    if (await db.scalars(q)).first() is not None:
        raise Problem(409, ErrorCode.DUPLICATE_NAME, "Already in the house")


async def _ensure_room(db: AsyncSession, house_id: uuid.UUID) -> None:
    count = await db.scalar(
        select(func.count()).select_from(Member).where(Member.house_id == house_id, Member.status.in_(COUNTED_STATUSES))
    )
    if (count or 0) >= MAX_MEMBERS_PER_HOUSE:
        raise Problem(422, ErrorCode.HOUSE_FULL, f"A house can have at most {MAX_MEMBERS_PER_HOUSE} people")


async def _new_member(
    db: AsyncSession,
    house_id: uuid.UUID,
    name: str,
    now: datetime,
    *,
    user_id: uuid.UUID | None,
    role: str = "member",
) -> Member:
    position = await db.scalar(select(func.count()).select_from(Member).where(Member.house_id == house_id)) or 0
    member = Member(
        id=uuid7(),
        house_id=house_id,
        user_id=user_id,
        display_name=name,
        color=MEMBER_COLORS[position % len(MEMBER_COLORS)],
        sort_order=position,
        role=role,
        status="active" if user_id else "placeholder",
        created_at=now,
        joined_at=now if user_id else None,
    )
    db.add(member)
    await db.flush()
    return member


def member_view(m: Member) -> dict[str, Any]:
    return {
        "id": str(m.id),
        "display_name": m.display_name,
        "initial": m.display_name[:1].upper(),
        "color": m.color,
        "sort_order": m.sort_order,
        "role": m.role,
        "status": m.status,
        "claimed": m.user_id is not None,
        "version": m.version,
    }


# ---- create --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Created:
    house_id: uuid.UUID
    member_id: uuid.UUID
    invite_token: str
    body: dict[str, Any]


async def create_house(
    writes: WriteContext,
    pepper: str,
    user: User,
    name: str,
    roommates: list[str],
    timezone: str,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    name = clean_name(name, max_len=40)
    names = [clean_name(n, max_len=24) for n in roommates]
    if len({n.lower() for n in names} | {user.display_name.lower()}) != len(names) + 1:
        raise Problem(409, ErrorCode.DUPLICATE_NAME, "Already in the house")
    if len(names) + 1 > MAX_MEMBERS_PER_HOUSE:
        raise Problem(422, ErrorCode.HOUSE_FULL, f"A house can have at most {MAX_MEMBERS_PER_HOUSE} people")
    house_id = uuid7()
    token, token_hash = invite_token(pepper, house_id, now)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        db = w.session
        db.add(
            House(
                id=house_id,
                name=name,
                invite_token_hash=token_hash,
                created_by=user.id,
                timezone=timezone,
                created_at=now,
            )
        )
        await db.flush()
        admin = await _new_member(
            db, house_id, clean_name(user.display_name, max_len=24), now, user_id=user.id, role="admin"
        )
        w.actor_member_id = admin.id
        members = [admin] + [await _new_member(db, house_id, n, now, user_id=None) for n in names]
        w.emit(
            "house_created",
            aggregate=("house", house_id, 1),
            data={"name": name},
            activity=Activity("house_created", payload={"name": name}, actor_user_id=user.id),
        )
        for m in members[1:]:
            w.emit(
                "member_added",
                aggregate=("member", m.id, 1),
                data=member_view(m),
                activity=Activity("member_added", payload={"name": m.display_name}, actor_user_id=user.id),
            )
        body = {
            "house": {"id": str(house_id), "name": name, "version": 1},
            "member_id": str(admin.id),
            "members": [member_view(m) for m in members],
            "invite_token": token,
        }
        w.respond(201, body)
    return body


# ---- reads -----------------------------------------------------------------------------------


async def house_view(db: AsyncSession, pepper: str, house: House, me: MemberCtx) -> dict[str, Any]:
    members = (await db.scalars(select(Member).where(Member.house_id == house.id).order_by(Member.sort_order))).all()
    token, _ = invite_token(pepper, house.id, house.invite_rotated_at or house.created_at)
    return {
        "house": {
            "id": str(house.id),
            "name": house.name,
            "version": house.version,
            "timezone": house.timezone,
        },
        "me": {"member_id": str(me.member_id), "role": me.role},
        "members": [member_view(m) for m in members if m.status != "removed"],
        "invite_token": token,
    }


# ---- guarded edits and transitions ---------------------------------------------------------------


async def _locked_house(w: HouseWrite) -> House:
    house = await w.session.get(House, w.house_id)
    if house is None or house.archived_at is not None:
        raise Problem(404, ErrorCode.NOT_FOUND, "House not found")
    return house


async def rename_house(
    writes: WriteContext, house_id: uuid.UUID, user: User, name: str, expected: int, cmid: str | None
) -> dict[str, Any]:
    name = clean_name(name, max_len=40)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        me = await membership(w.session, house_id, user.id)
        require(Action.HOUSE_RENAME, actor=me)
        assert me is not None
        w.actor_member_id = me.member_id
        house = await _locked_house(w)
        if house.version != expected:
            raise Problem(
                409,
                ErrorCode.VERSION_CONFLICT,
                "Someone just changed this",
                extra={"current": {"name": house.name, "version": house.version}},
            )
        if house.name == name:
            return {"name": name, "version": house.version}
        house.name = name
        w.touch_house()  # version bumped with the sequence, house lock last
        w.emit(
            "house_renamed",
            aggregate=("house", house_id, expected + 1),
            data={"name": name},
            activity=Activity("house_renamed", payload={"name": name}, actor_user_id=user.id),
        )
    return {"name": name, "version": expected + 1}


async def rotate_invite(
    writes: WriteContext,
    pepper: str,
    house_id: uuid.UUID,
    user: User,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await membership(w.session, house_id, user.id)
        require(Action.INVITE_ROTATE, actor=me)
        assert me is not None
        w.actor_member_id = me.member_id
        token, token_hash = invite_token(pepper, house_id, now)
        await w.session.execute(
            update(House).where(House.id == house_id).values(invite_token_hash=token_hash, invite_rotated_at=now)
        )
        w.emit(
            "invite_rotated",
            aggregate=("house", house_id, None),
            activity=Activity("invite_rotated", actor_user_id=user.id),
        )
        body = {"invite_token": token}
        w.respond(200, body)
    return body


async def add_placeholder(
    writes: WriteContext,
    house_id: uuid.UUID,
    user: User,
    name: str,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    name = clean_name(name, max_len=24)
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await membership(w.session, house_id, user.id)
        require(Action.MEMBER_ADD, actor=me)
        assert me is not None
        w.actor_member_id = me.member_id
        await _ensure_name_free(w.session, house_id, name)
        await _ensure_room(w.session, house_id)
        m = await _new_member(w.session, house_id, name, now, user_id=None)
        w.emit(
            "member_added",
            aggregate=("member", m.id, 1),
            data=member_view(m),
            activity=Activity("member_added", payload={"name": name}, actor_user_id=user.id),
        )
        body = member_view(m)
        w.respond(201, body)
    return body


async def rename_member(
    writes: WriteContext,
    house_id: uuid.UUID,
    user: User,
    member_id: uuid.UUID,
    name: str,
    expected: int,
    cmid: str | None,
) -> dict[str, Any]:
    name = clean_name(name, max_len=24)
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        target = await _member_for_update(w.session, house_id, member_id)
        me = await membership(w.session, house_id, user.id)
        require(Action.MEMBER_RENAME, actor=me, target=target_of(target))
        assert me is not None
        w.actor_member_id = me.member_id
        if target.version != expected:
            raise Problem(
                409,
                ErrorCode.VERSION_CONFLICT,
                "Someone just changed this",
                extra={"current": member_view(target)},
            )
        await _ensure_name_free(w.session, house_id, name, except_id=member_id)
        old = target.display_name
        target.display_name = name
        target.version += 1
        w.emit(
            "member_renamed",
            aggregate=("member", member_id, target.version),
            data=member_view(target),
            activity=Activity("member_renamed", payload={"from": old, "to": name}, actor_user_id=user.id),
        )
        body = member_view(target)
    return body


async def _blocked_as_payer(db: AsyncSession, member_id: uuid.UUID) -> bool:
    return (
        await db.scalars(
            select(Bill.id).where(Bill.payer_member_id == member_id, Bill.state == "open", Bill.deleted_at.is_(None))
        )
    ).first() is not None


async def remove_member(
    writes: WriteContext, house_id: uuid.UUID, user: User, member_id: uuid.UUID, cmid: str | None
) -> dict[str, Any]:
    async with house_write(writes, house_id, client_mutation_id=cmid) as w:
        target = await _member_for_update(w.session, house_id, member_id)
        me = await membership(w.session, house_id, user.id)
        require(Action.MEMBER_REMOVE, actor=me, target=target_of(target))
        assert me is not None
        w.actor_member_id = me.member_id
        if target.role == "admin":
            raise Problem(409, ErrorCode.CONFLICT, "Make someone else admin first")
        if await _blocked_as_payer(w.session, member_id):
            raise Problem(
                409,
                ErrorCode.PAYER_ON_OPEN_BILLS,
                f"{target.display_name} paid for open tabs. Settle them or move the payer first.",
            )
        target.status = "removed"
        target.version += 1
        w.emit(
            "member_removed",
            aggregate=("member", member_id, target.version),
            data=member_view(target),
            activity=Activity("member_removed", payload={"name": target.display_name}, actor_user_id=user.id),
        )
        body = member_view(target)
    return body


async def leave_house(
    writes: WriteContext, house_id: uuid.UUID, user: User, idem: Idempotency, cmid: str | None
) -> dict[str, Any]:
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await membership(w.session, house_id, user.id)
        require(Action.HOUSE_LEAVE, actor=me)
        assert me is not None
        target = await _member_for_update(w.session, house_id, me.member_id)
        w.actor_member_id = me.member_id
        if target.role == "admin":
            others = await w.session.scalar(
                select(func.count())
                .select_from(Member)
                .where(Member.house_id == house_id, Member.status == "active", Member.id != target.id)
            )
            if others:
                raise Problem(409, ErrorCode.CONFLICT, "Make someone else admin before you leave")
        if await _blocked_as_payer(w.session, target.id):
            raise Problem(
                409,
                ErrorCode.PAYER_ON_OPEN_BILLS,
                "You paid for open tabs. Settle them or move the payer before you leave.",
            )
        target.status = "left"
        target.version += 1
        w.emit(
            "member_left",
            aggregate=("member", target.id, target.version),
            data=member_view(target),
            activity=Activity("member_left", payload={"name": target.display_name}, actor_user_id=user.id),
        )
        body = member_view(target)
        w.respond(200, body)
    return body


async def unclaim(
    writes: WriteContext,
    house_id: uuid.UUID,
    user: User,
    member_id: uuid.UUID,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-09: return a wrongly claimed name to a placeholder; edits made under it stay in the log."""
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        target = await _member_for_update(w.session, house_id, member_id)
        me = await membership(w.session, house_id, user.id)
        require(Action.MEMBER_UNCLAIM, actor=me, target=target_of(target))
        assert me is not None
        w.actor_member_id = me.member_id
        previous_user = target.user_id
        target.user_id = None
        target.status = "placeholder"
        target.joined_at = None
        target.version += 1
        w.emit(
            "member_unclaimed",
            aggregate=("member", member_id, target.version),
            data=member_view(target),
            activity=Activity(
                "member_unclaimed",
                actor_user_id=user.id,
                payload={"name": target.display_name, "previous_user_id": str(previous_user)},
            ),
        )
        body = member_view(target)
        w.respond(200, body)
    return body


async def transfer_admin(
    writes: WriteContext,
    house_id: uuid.UUID,
    user: User,
    member_id: uuid.UUID,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        me = await membership(w.session, house_id, user.id)
        target = await _member_for_update(w.session, house_id, member_id)
        require(Action.ADMIN_TRANSFER, actor=me, target=target_of(target))
        assert me is not None
        current = await _member_for_update(w.session, house_id, me.member_id)
        w.actor_member_id = me.member_id
        # Demote first: the partial unique index allows only one active admin at any moment.
        current.role = "member"
        current.version += 1
        await w.session.flush()
        target.role = "admin"
        target.version += 1
        w.emit(
            "admin_transferred",
            aggregate=("member", member_id, target.version),
            data={"from": str(current.id), "to": str(target.id)},
            activity=Activity(
                "admin_transferred",
                actor_user_id=user.id,
                payload={"from": current.display_name, "to": target.display_name},
            ),
        )
        body = {"admin_member_id": str(target.id)}
        w.respond(200, body)
    return body


# ---- joining -------------------------------------------------------------------------------------


async def house_by_invite(db: AsyncSession, token_hash: str) -> House | None:
    return (
        await db.scalars(select(House).where(House.invite_token_hash == token_hash, House.archived_at.is_(None)))
    ).first()


async def join_view(db: AsyncSession, house: House, user: User | None) -> dict[str, Any]:
    members = (await db.scalars(select(Member).where(Member.house_id == house.id).order_by(Member.sort_order))).all()
    admin = next((m for m in members if m.role == "admin" and m.status == "active"), None)
    mine = next((m for m in members if user is not None and m.user_id == user.id), None)
    return {
        "house": {"id": str(house.id), "name": house.name},
        "admin_name": admin.display_name if admin else None,
        "member_count": sum(1 for m in members if m.status in COUNTED_STATUSES),
        "placeholders": [member_view(m) for m in members if m.status == "placeholder"],
        "signed_in": user is not None,
        "my_member": member_view(mine) if mine and mine.status == "active" else None,
    }


async def claim(
    writes: WriteContext,
    house_id: uuid.UUID,
    user: User,
    member_id: uuid.UUID | None,
    new_name: str | None,
    now: datetime,
    idem: Idempotency,
    cmid: str | None,
) -> dict[str, Any]:
    """FR-05: pick an unclaimed placeholder or "I'm new". First claim wins (EDGE-15-18, SYNC-11)."""
    if (member_id is None) == (new_name is None):
        raise Problem(422, ErrorCode.VALIDATION_FAILED, "Pick a name or add yours")
    async with house_write(writes, house_id, client_mutation_id=cmid, idempotency=idem) as w:
        db = w.session
        existing = (
            await db.scalars(
                select(Member).where(Member.house_id == house_id, Member.user_id == user.id).with_for_update()
            )
        ).first()
        if existing is not None and existing.status == "active":
            raise Problem(
                409,
                ErrorCode.ALREADY_MEMBER,
                "You're already in this house",
                extra={"member": member_view(existing)},
            )
        if existing is not None:
            # Rejoin reactivates the old row (ADR-0003) whatever was picked.
            await _ensure_room(db, house_id)
            existing.status = "active"
            existing.joined_at = now
            existing.version += 1
            member, event = existing, "member_rejoined"
        elif member_id is not None:
            member = await _member_for_update(db, house_id, member_id)
            if member.user_id is not None or member.status != "placeholder":
                raise Problem(
                    409,
                    ErrorCode.ALREADY_CLAIMED,
                    "Already claimed — is this you?",
                    extra={"member": member_view(member)},
                )
            member.user_id = user.id
            member.status = "active"
            member.joined_at = now
            member.version += 1
            event = "member_claimed"
        else:
            assert new_name is not None
            name = clean_name(new_name, max_len=24)
            await _ensure_name_free(db, house_id, name)
            await _ensure_room(db, house_id)
            member = await _new_member(db, house_id, name, now, user_id=user.id)
            event = "member_joined"
        w.actor_member_id = member.id
        w.emit(
            event,
            aggregate=("member", member.id, member.version),
            data=member_view(member),
            activity=Activity(event, payload={"name": member.display_name}, actor_user_id=user.id),
            notify=["joined"],
        )
        body = {"house_id": str(house_id), "member": member_view(member)}
        w.respond(200, body)
    return body
