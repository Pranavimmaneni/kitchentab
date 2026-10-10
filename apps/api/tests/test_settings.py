"""Settings (PRD §14A Settings, FR-83; TEST_PLAN SET): one place for your name and preferences."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import HouseEvent
from tests.harness.api import Api
from tests.harness.tags import fr, tid

ApiFactory = Callable[[], Api]


async def names(who: Api, house_id: str) -> list[str]:
    house: dict[str, Any] = (await who.get(f"/api/v1/houses/{house_id}")).json()
    return [m["display_name"] for m in house["members"]]


@tid("SET-01")
@fr("FR-83")
async def test_SET_01_a_new_name_follows_you_into_every_house(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    """Save name in Settings renames your member in each house you live in (one house write and one
    member_renamed event per house). A house where someone else has that name keeps your old one
    there, and the answer says which."""
    me = api()
    await me.sign_in("pranav@example.com")
    apt = (await me.create_house(roommates=["Arjun"]))["house"]["id"]
    beach = (await me.post("/api/v1/houses", {"name": "Beach house", "roommates": ["Sam"]})).json()["house"]["id"]
    assert "Pranav" in await names(me, apt) and "Pranav" in await names(me, beach)

    r = await me.call("PATCH", "/api/v1/me", {"display_name": "  Pranav   R  "})
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "Pranav R"
    assert sorted(r.json()["name_updated_in"]) == ["Apt 4B", "Beach house"] and r.json()["name_taken_in"] == []
    assert "Pranav R" in await names(me, apt) and "Pranav R" in await names(me, beach)
    async with sessions() as db:
        renamed = (await db.scalars(select(HouseEvent.house_id).where(HouseEvent.type == "member_renamed"))).all()
    assert sorted(map(str, renamed)) == sorted([apt, beach]), "one event per house"

    # "Sam" is taken in the beach house: it changes in Apt 4B only.
    r = await me.call("PATCH", "/api/v1/me", {"display_name": "Sam"})
    assert r.json()["name_updated_in"] == ["Apt 4B"] and r.json()["name_taken_in"] == ["Beach house"]
    assert "Sam" in await names(me, apt) and "Pranav R" in await names(me, beach)

    # A long account name is shortened to the 24-character house limit.
    r = await me.call("PATCH", "/api/v1/me", {"display_name": "Pranav Ramachandran Immaneni"})
    assert r.json()["display_name"] == "Pranav Ramachandran Immaneni"
    assert "Pranav Ramachandran Imma" in await names(me, apt)

    # Saving the same name again writes nothing.
    async with sessions() as db:
        before = len((await db.scalars(select(HouseEvent.seq))).all())
    r = await me.call("PATCH", "/api/v1/me", {"display_name": "Pranav Ramachandran Immaneni"})
    assert r.json()["name_updated_in"] == []
    async with sessions() as db:
        assert len((await db.scalars(select(HouseEvent.seq))).all()) == before
