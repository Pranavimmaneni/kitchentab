"""Houses, members, invite links, joining (PRD §11; FR-02..FR-09)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.deps import (
    Actor,
    client_ip,
    client_mutation_id,
    ctx,
    idempotency,
    if_match,
    optional_actor,
    rate_limit,
    require_actor,
)
from app.errors import ErrorCode, Problem
from app.links import looks_like_token
from app.models import House, Member
from app.policy import Action, require
from app.services import houses as svc
from app.tokens import hash_token

router = APIRouter(tags=["houses"])
LINK_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex",
    "Cache-Control": "private, no-store",
}
BAD_LINK_LIMIT = 20


class CreateHouse(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    roommates: list[str] = Field(default_factory=list, max_length=19)
    timezone: str = Field(default="America/New_York", max_length=64)


@router.post("/houses", status_code=201)
async def create_house(body: CreateHouse, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.create_house(
        c.writes,
        c.settings.token_hash_pepper.get_secret_value(),
        actor.user,
        body.name,
        body.roommates,
        body.timezone,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


@router.get("/houses")
async def list_houses(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> list[dict[str, Any]]:
    c = ctx(request)
    async with c.sessions() as db:
        rows = (
            await db.execute(
                select(House, Member)
                .join(Member, Member.house_id == House.id)
                .where(Member.user_id == actor.user.id, Member.status == "active", House.archived_at.is_(None))
                .order_by(Member.joined_at.desc())
            )
        ).all()
    return [{"id": str(h.id), "name": h.name, "member_id": str(m.id), "role": m.role} for h, m in rows]


@router.get("/houses/{house_id}")
async def get_house(
    house_id: uuid.UUID, request: Request, response: Response, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    c = ctx(request)
    async with c.sessions() as db:
        me = await svc.membership(db, house_id, actor.user.id)
        require(Action.HOUSE_READ, actor=me)
        assert me is not None
        house = await db.get(House, house_id)
        if house is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "House not found")
        view = await svc.house_view(db, c.settings.token_hash_pepper.get_secret_value(), house, me)
    response.headers["ETag"] = f'"{house.version}"'
    return view


class Rename(BaseModel):
    name: str = Field(min_length=1, max_length=40)


@router.patch("/houses/{house_id}")
async def rename_house(
    house_id: uuid.UUID, body: Rename, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> dict[str, Any]:
    c = ctx(request)
    return await svc.rename_house(
        c.writes, house_id, actor.user, body.name, if_match(request), client_mutation_id(request)
    )


@router.post("/houses/{house_id}/invite/rotate")
async def rotate_invite(house_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.rotate_invite(
        c.writes,
        c.settings.token_hash_pepper.get_secret_value(),
        house_id,
        actor.user,
        c.clock.now(),
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


class NewMember(BaseModel):
    name: str = Field(min_length=1, max_length=24)


@router.post("/houses/{house_id}/members", status_code=201)
async def add_member(
    house_id: uuid.UUID, body: NewMember, request: Request, actor: Annotated[Actor, Depends(require_actor)]
) -> Any:
    c = ctx(request)
    return await svc.add_placeholder(
        c.writes,
        house_id,
        actor.user,
        body.name,
        c.clock.now(),
        idempotency(request, actor, body.model_dump()),
        client_mutation_id(request),
    )


class RenameMember(BaseModel):
    name: str = Field(min_length=1, max_length=24)


@router.patch("/houses/{house_id}/members/{member_id}")
async def rename_member(
    house_id: uuid.UUID,
    member_id: uuid.UUID,
    body: RenameMember,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
) -> dict[str, Any]:
    c = ctx(request)
    return await svc.rename_member(
        c.writes, house_id, actor.user, member_id, body.name, if_match(request), client_mutation_id(request)
    )


@router.delete("/houses/{house_id}/members/{member_id}")
async def remove_member(
    house_id: uuid.UUID,
    member_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
) -> dict[str, Any]:
    c = ctx(request)
    return await svc.remove_member(c.writes, house_id, actor.user, member_id, client_mutation_id(request))


@router.post("/houses/{house_id}/members/{member_id}/unclaim")
async def unclaim(
    house_id: uuid.UUID,
    member_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
) -> Any:
    c = ctx(request)
    return await svc.unclaim(
        c.writes,
        house_id,
        actor.user,
        member_id,
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


@router.post("/houses/{house_id}/members/{member_id}/make-admin")
async def make_admin(
    house_id: uuid.UUID,
    member_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_actor)],
) -> Any:
    c = ctx(request)
    return await svc.transfer_admin(
        c.writes,
        house_id,
        actor.user,
        member_id,
        idempotency(request, actor, None),
        client_mutation_id(request),
    )


@router.post("/houses/{house_id}/leave")
async def leave(house_id: uuid.UUID, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    c = ctx(request)
    return await svc.leave_house(
        c.writes, house_id, actor.user, idempotency(request, actor, None), client_mutation_id(request)
    )


# ---- invite links ----------------------------------------------------------------------------


async def _house_for_token(request: Request, token: str) -> House:
    """Constant-time lookup by hash; 20 bad tokens per IP per hour locks the IP out (LINK-06)."""
    c = ctx(request)
    ip = client_ip(request)
    house = None
    if looks_like_token(token):
        async with c.sessions() as db:
            house = await svc.house_by_invite(db, hash_token(token, c.settings.token_hash_pepper.get_secret_value()))
    if house is None:
        await rate_limit(request, "bad_link", ip, limit=BAD_LINK_LIMIT, window=timedelta(hours=1))
        raise Problem(
            410,
            ErrorCode.LINK_EXPIRED,
            "This link has expired. Ask your house for a new one.",
            headers=LINK_HEADERS,
        )
    return house


async def _lockout_check(request: Request) -> None:
    import hashlib

    from app import ratelimit
    from app.deps import ctx as get_ctx

    c = get_ctx(request)
    key = hashlib.sha256(f"bad_link:{client_ip(request)}".encode()).hexdigest()
    async with c.sessions() as db:
        seen = await ratelimit.count(db, c.clock, "bad_link", key, window=timedelta(hours=1))
    if seen >= BAD_LINK_LIMIT:
        raise Problem(
            429,
            ErrorCode.RATE_LIMITED,
            "Too many tries",
            "Try again later.",
            headers={**LINK_HEADERS, "Retry-After": "3600"},
        )


@router.get("/join/{token}")
async def join_info(
    token: str, request: Request, response: Response, actor: Annotated[Actor | None, Depends(optional_actor)]
) -> dict[str, Any]:
    await _lockout_check(request)
    house = await _house_for_token(request, token)
    async with ctx(request).sessions() as db:
        view = await svc.join_view(db, house, actor.user if actor else None)
    response.headers.update(LINK_HEADERS)
    return view


class Claim(BaseModel):
    member_id: uuid.UUID | None = None
    new_name: str | None = Field(default=None, min_length=1, max_length=24)


@router.post("/join/{token}/claim")
async def claim(token: str, body: Claim, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> Any:
    await _lockout_check(request)
    house = await _house_for_token(request, token)
    c = ctx(request)
    return await svc.claim(
        c.writes,
        house.id,
        actor.user,
        body.member_id,
        body.new_name,
        c.clock.now(),
        idempotency(request, actor, body.model_dump(mode="json")),
        client_mutation_id(request),
    )
