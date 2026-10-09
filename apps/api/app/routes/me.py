"""The signed-in user's profile and preferences (PRD §11 `/me`)."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.deps import Actor, ctx, require_actor
from app.errors import ErrorCode, Problem
from app.models import House, Member, User

router = APIRouter(tags=["me"])


def user_view(user: User) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "display_name": user.display_name,
        "email": user.email,
        "phone": user.phone_e164,
        "theme": user.theme,
        "timezone": user.timezone,
        "notify_email": user.notify_email,
        "share_by_default": user.share_by_default,
    }


@router.get("/me")
async def me(request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> dict[str, Any]:
    async with ctx(request).sessions() as db:
        rows = (
            await db.execute(
                select(House.id, House.name, Member.id, Member.role)
                .join(Member, Member.house_id == House.id)
                .where(Member.user_id == actor.user.id, Member.status == "active", House.archived_at.is_(None))
                .order_by(Member.joined_at.desc())
            )
        ).all()
    return {
        "user": user_view(actor.user),
        "houses": [{"id": str(h), "name": n, "member_id": str(m), "role": r} for h, n, m, r in rows],
    }


class Profile(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    theme: Literal["system", "light", "dark"] | None = None
    timezone: str | None = None
    notify_email: bool | None = None
    share_by_default: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str | None) -> str | None:
        if value is not None and value not in available_timezones():
            raise ValueError("unknown time zone")
        return value


@router.patch("/me")
async def update_me(body: Profile, request: Request, actor: Annotated[Actor, Depends(require_actor)]) -> dict[str, Any]:
    """Set-state fields on the user's own row (last write wins; no other writer)."""
    async with ctx(request).sessions() as db, db.begin():
        user = await db.get(User, actor.user.id, with_for_update=True)
        if user is None:
            raise Problem(404, ErrorCode.NOT_FOUND, "Account not found")
        for field, value in body.model_dump(exclude_none=True).items():
            setattr(user, field, " ".join(value.split()) if field == "display_name" else value)
        return user_view(user)
