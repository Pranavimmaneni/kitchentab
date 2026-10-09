"""Minimal row builders for tests that need a house before the real services exist."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.models import House, Member, User

MEMBER_COLORS = ["#C6F432", "#6A4CFF", "#FF8A65", "#7FD3F7", "#F7D046", "#FF6FB5", "#4FD1A5", "#B0A6FF"]


async def make_house(
    sessions: async_sessionmaker[AsyncSession], now: datetime, *, members: int = 1, name: str = "Apt 4B"
) -> tuple[House, list[Member]]:
    async with sessions() as s, s.begin():
        user = User(id=uuid7(), display_name="Pranav", created_at=now)
        house = House(
            id=uuid7(), name=name, created_by=user.id, created_at=now,
            invite_token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
        )
        s.add(user)
        await s.flush()
        s.add(house)
        await s.flush()
        rows = [
            Member(
                id=uuid7(), house_id=house.id, user_id=user.id if i == 0 else None,
                display_name=f"Member{i}", color=MEMBER_COLORS[i % 8], sort_order=i,
                role="admin" if i == 0 else "member", status="active" if i == 0 else "placeholder",
                created_at=now, joined_at=now if i == 0 else None,
            )
            for i in range(members)
        ]
        s.add_all(rows)
    return house, rows
