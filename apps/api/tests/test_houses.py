"""Houses, members, invite links and claims (PRD §5.1, §7, §8; TEST_PLAN 3.3)."""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ids import uuid7
from app.models import ActivityEvent, Bill, HouseEvent, Member
from app.tokens import TOKEN_LENGTH
from tests.harness.api import Api
from tests.harness.race import run_concurrently, sync_iterations
from tests.harness.tags import fr, tid

ApiFactory = Callable[[], Api]


async def _admin_with_house(api: ApiFactory, roommates: list[str] | None = None) -> tuple[Api, dict[str, Any]]:
    admin = api()
    await admin.sign_in("pranav@example.com")
    return admin, await admin.create_house(roommates=roommates)


def _member(house: dict[str, Any], name: str) -> dict[str, Any]:
    return next(m for m in house["members"] if m["display_name"] == name)


@tid("HOUSE-01")
@fr("FR-02")
async def test_HOUSE_01_create_house_creator_is_only_admin(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    admin, house = await _admin_with_house(api)
    assert house["house"]["name"] == "Apt 4B"
    roles = [(m["display_name"], m["role"], m["status"]) for m in house["members"]]
    assert roles == [
        ("Pranav", "admin", "active"),
        ("Arjun", "member", "placeholder"),
        ("Meera", "member", "placeholder"),
        ("Kabir", "member", "placeholder"),
    ]
    assert [m["color"] for m in house["members"]] == ["#C6F432", "#6A4CFF", "#FF8A65", "#7FD3F7"]
    async with sessions() as db:
        seqs = (await db.scalars(select(HouseEvent.seq).order_by(HouseEvent.seq))).all()
    assert list(seqs) == [1, 2, 3, 4], "one event per write item, gapless (CLAUDE.md rule 3)"
    for bad in ("", "x" * 41):
        r = await admin.post("/api/v1/houses", {"name": bad})
        assert r.status_code == 422


@tid("HOUSE-02")
@fr("FR-03")
async def test_HOUSE_02_placeholders_and_case_insensitive_duplicates(api: ApiFactory) -> None:
    admin, house = await _admin_with_house(api, roommates=["Arjun"])
    hid = house["house"]["id"]
    added = await admin.post(f"/api/v1/houses/{hid}/members", {"name": "Meera"})
    assert added.status_code == 201 and added.json()["status"] == "placeholder"
    dup = await admin.post(f"/api/v1/houses/{hid}/members", {"name": "meera"})
    assert dup.status_code == 409 and dup.json()["title"] == "Already in the house"
    assert (await admin.post("/api/v1/houses", {"name": "B", "roommates": ["Sam", "SAM"]})).status_code == 409


async def _join(
    api: ApiFactory, token: str, email: str, pick: str | None = None, new: str | None = None
) -> tuple[Api, Any]:
    who = api()
    await who.sign_in(email)
    info = (await who.get(f"/api/v1/join/{token}")).json()
    member_id = next((m["id"] for m in info["placeholders"] if m["display_name"] == pick), None) if pick else None
    resp = await who.post(f"/api/v1/join/{token}/claim", {"member_id": member_id} if pick else {"new_name": new})
    return who, resp


@tid("HOUSE-03")
@fr("FR-05")
async def test_HOUSE_03_claim_placeholder_via_invite(api: ApiFactory) -> None:
    _, house = await _admin_with_house(api)
    token = house["invite_token"]
    assert len(token) == TOKEN_LENGTH
    info = (await api().get(f"/api/v1/join/{token}")).json()
    assert info["house"]["name"] == "Apt 4B" and info["admin_name"] == "Pranav" and info["member_count"] == 4
    assert [m["display_name"] for m in info["placeholders"]] == ["Arjun", "Meera", "Kabir"]
    arjun, resp = await _join(api, token, "arjun@example.com", pick="Arjun")
    assert resp.status_code == 200 and resp.json()["member"]["status"] == "active"
    after = (await api().get(f"/api/v1/join/{token}")).json()
    assert [m["display_name"] for m in after["placeholders"]] == ["Meera", "Kabir"], "claimed names leave the picker"
    assert (await arjun.get(f"/api/v1/houses/{house['house']['id']}")).status_code == 200


@tid("HOUSE-04")
@fr("FR-05")
async def test_HOUSE_04_im_new_creates_a_member(api: ApiFactory) -> None:
    _, house = await _admin_with_house(api)
    _, resp = await _join(api, house["invite_token"], "dev@example.com", new="Dev")
    assert resp.status_code == 200
    assert resp.json()["member"] | {"id": None} == resp.json()["member"] | {
        "id": None,
        "display_name": "Dev",
        "status": "active",
        "role": "member",
        "claimed": True,
    }


@tid("HOUSE-05", "EDGE-15-19", "EDGE-15-21")
@fr("FR-09")
async def test_HOUSE_05_admin_unclaims_wrong_claim_with_audit(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    admin, house = await _admin_with_house(api)
    hid = house["house"]["id"]
    _, resp = await _join(api, house["invite_token"], "wrong@example.com", pick="Meera")
    meera_id = resp.json()["member"]["id"]
    r = await admin.post(f"/api/v1/houses/{hid}/members/{meera_id}/unclaim")
    assert r.status_code == 200 and r.json()["status"] == "placeholder" and r.json()["claimed"] is False
    async with sessions() as db:
        types = (await db.scalars(select(ActivityEvent.type).order_by(ActivityEvent.created_at))).all()
    assert "member_claimed" in types and types[-1] == "member_unclaimed", "the wrong claim stays in the log"
    # The real Meera (new sign-in, e.g. lost access to the old email) can claim it now.
    _, again = await _join(api, house["invite_token"], "meera@example.com", pick="Meera")
    assert again.status_code == 200


@tid("HOUSE-06", "EDGE-15-13")
@fr("FR-06")
async def test_HOUSE_06_remove_member_keeps_their_rows(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    admin, house = await _admin_with_house(api)
    hid = house["house"]["id"]
    kabir_id = _member(house, "Kabir")["id"]
    r = await admin.call("DELETE", f"/api/v1/houses/{hid}/members/{kabir_id}")
    assert r.status_code == 200 and r.json()["status"] == "removed"
    view = (await admin.get(f"/api/v1/houses/{hid}")).json()
    assert "Kabir" not in [m["display_name"] for m in view["members"]], "excluded from new bills and pickers"
    async with sessions() as db:
        row = await db.get(Member, kabir_id)
    assert row is not None and row.status == "removed", "the row (and anything pointing at it) stays"


async def _open_bill_paid_by(
    sessions: async_sessionmaker[AsyncSession], house_id: str, member_id: str, user_id: str
) -> None:
    from datetime import UTC, date, datetime

    now = datetime.now(UTC)
    async with sessions() as db, db.begin():
        db.add(
            Bill(
                id=uuid7(),
                house_id=house_id,
                payer_member_id=member_id,
                created_by=user_id,
                store_name="ShopRite",
                purchase_date=date(2026, 10, 2),
                state="open",
                sent_at=now,
                created_at=now,
                updated_at=now,
            )
        )


@tid("HOUSE-07", "EDGE-15-23")
@fr("FR-06")
async def test_HOUSE_07_leave_blocked_while_payer_on_open_bills(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    admin, house = await _admin_with_house(api)
    hid = house["house"]["id"]
    arjun, resp = await _join(api, house["invite_token"], "arjun@example.com", pick="Arjun")
    arjun_id = resp.json()["member"]["id"]
    await _open_bill_paid_by(sessions, hid, arjun_id, arjun.user_id or "")
    blocked = await arjun.post(f"/api/v1/houses/{hid}/leave")
    assert blocked.status_code == 409 and blocked.json()["code"] == "PAYER_ON_OPEN_BILLS"
    assert "Settle" in blocked.json()["title"]
    admin_leave = await admin.post(f"/api/v1/houses/{hid}/leave")
    assert admin_leave.status_code == 409, "the admin must hand over admin first"
    meera, _ = await _join(api, house["invite_token"], "meera@example.com", pick="Meera")
    left = await meera.post(f"/api/v1/houses/{hid}/leave")
    assert left.status_code == 200 and left.json()["status"] == "left"
    # Rejoining reactivates the same member row (ADR-0003).
    again = await meera.post(f"/api/v1/join/{house['invite_token']}/claim", {"new_name": "Meera"})
    assert again.status_code == 200 and again.json()["member"]["id"] == left.json()["id"]


@tid("HOUSE-08")
@fr("FR-07")
async def test_HOUSE_08_transfer_admin_keeps_exactly_one(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    admin, house = await _admin_with_house(api)
    hid = house["house"]["id"]
    placeholder_id = _member(house, "Kabir")["id"]
    refused = await admin.post(f"/api/v1/houses/{hid}/members/{placeholder_id}/make-admin")
    assert refused.status_code == 409, "admin goes only to someone who has joined"
    _, resp = await _join(api, house["invite_token"], "arjun@example.com", pick="Arjun")
    arjun_id = resp.json()["member"]["id"]
    ok = await admin.post(f"/api/v1/houses/{hid}/members/{arjun_id}/make-admin")
    assert ok.status_code == 200
    async with sessions() as db:
        admins = (await db.scalars(select(Member.id).where(Member.role == "admin", Member.status == "active"))).all()
    assert [str(a) for a in admins] == [arjun_id]
    assert (await admin.post(f"/api/v1/houses/{hid}/members", {"name": "Zed"})).status_code == 403
    async with sessions() as db:
        with pytest.raises(Exception, match="uq_members_one_admin_per_house"):
            async with db.begin():
                await db.execute(
                    text("UPDATE members SET role = 'admin' WHERE house_id = :h AND status = 'active'"), {"h": hid}
                )


@tid("HOUSE-10")
@fr("FR-03")
async def test_HOUSE_10_max_twenty_people(api: ApiFactory) -> None:
    admin, house = await _admin_with_house(api, roommates=[f"R{i}" for i in range(19)])
    assert len(house["members"]) == 20
    r = await admin.post(f"/api/v1/houses/{house['house']['id']}/members", {"name": "Twentyone"})
    assert r.status_code == 422 and r.json()["code"] == "HOUSE_FULL"
    too_many = await admin.post("/api/v1/houses", {"name": "Big", "roommates": [f"S{i}" for i in range(20)]})
    assert too_many.status_code == 422


@tid("LINK-01")
@fr("FR-04")
async def test_LINK_01_tokens_random_hashed_constant_time(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    _, house = await _admin_with_house(api)
    token = house["invite_token"]
    async with sessions() as db:
        stored = (await db.execute(text("SELECT invite_token_hash FROM houses"))).scalar_one()
    assert token not in stored and len(stored) == 64
    import inspect

    from app import tokens

    assert "compare_digest" in inspect.getsource(tokens.token_matches)
    _, other = await _admin_with_house(api)
    assert other["invite_token"] != token


@tid("LINK-02")
@fr("FR-04")
async def test_LINK_02_rotated_invite_returns_410(api: ApiFactory) -> None:
    admin, house = await _admin_with_house(api)
    old = house["invite_token"]
    rotated = await admin.post(f"/api/v1/houses/{house['house']['id']}/invite/rotate")
    new = rotated.json()["invite_token"]
    assert new != old
    gone = await api().get(f"/api/v1/join/{old}")
    assert gone.status_code == 410 and gone.json()["title"] == "This link has expired. Ask your house for a new one."
    assert (await api().get(f"/api/v1/join/{new}")).status_code == 200
    # Members see the current link again later without it being stored (ADR-0018).
    assert (await admin.get(f"/api/v1/houses/{house['house']['id']}")).json()["invite_token"] == new


@tid("LINK-04")
async def test_LINK_04_link_responses_noindex_no_referrer(api: ApiFactory) -> None:
    _, house = await _admin_with_house(api)
    r = await api().get(f"/api/v1/join/{house['invite_token']}")
    assert r.headers["Referrer-Policy"] == "no-referrer" and r.headers["X-Robots-Tag"] == "noindex"
    bad = await api().get("/api/v1/join/ABCDEFGHIJKLMNOPQRSTUV")
    assert bad.headers["X-Robots-Tag"] == "noindex"


@tid("LINK-06", "SEC-14")
async def test_LINK_06_twenty_bad_tokens_lock_the_ip(api: ApiFactory) -> None:
    _, house = await _admin_with_house(api)
    probe = api()
    for i in range(20):
        assert (await probe.get(f"/api/v1/join/BAD{i:019d}")).status_code == 410
    locked = await probe.get(f"/api/v1/join/{house['invite_token']}")
    assert locked.status_code == 429, "even a valid token is refused during the lockout"


async def _claim_as(claimants: tuple[Api, Api], url: str, body: dict[str, Any], i: int) -> Any:
    return await claimants[i].post(url, body)


@tid("SYNC-11", "EDGE-15-18")
@pytest.mark.sync
@fr("FR-05")
async def test_SYNC_11_two_people_claim_the_same_placeholder(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    for _ in range(sync_iterations()):
        _, house = await _admin_with_house(api)
        url = f"/api/v1/join/{house['invite_token']}/claim"
        body = {"member_id": _member(house, "Meera")["id"]}
        claimants = (api(), api())
        for who in claimants:
            await who.sign_in(f"{uuid7()}@example.com")
        outcomes = await run_concurrently(2, functools.partial(_claim_as, claimants, url, body))
        codes = sorted(o.value.status_code for o in outcomes if o.value is not None)
        assert codes == [200, 409]
        loser = next(o.value for o in outcomes if o.value is not None and o.value.status_code == 409)
        assert loser.json()["code"] == "ALREADY_CLAIMED" and loser.json()["title"] == "Already claimed — is this you?"
        async with sessions() as db:
            claimed = await db.scalar(
                select(func.count())
                .select_from(Member)
                .where(Member.id == body["member_id"], Member.user_id.is_not(None))
            )
        assert claimed == 1


@tid("EDGE-15-20")
@fr("FR-05")
async def test_EDGE_15_20_forwarded_link_can_view_but_not_edit(api: ApiFactory) -> None:
    _, house = await _admin_with_house(api)
    stranger = api()
    assert (await stranger.get(f"/api/v1/join/{house['invite_token']}")).status_code == 200
    hid = house["house"]["id"]
    assert (await stranger.get(f"/api/v1/houses/{hid}")).status_code == 401
    await stranger.sign_in("stranger@example.com")
    r = await stranger.get(f"/api/v1/houses/{hid}")
    assert r.status_code == 403 and r.json()["code"] == "NOT_A_MEMBER"
    assert (await stranger.post(f"/api/v1/houses/{hid}/members", {"name": "X"})).json()["code"] == "NOT_A_MEMBER"


async def test_rename_house_is_guarded_by_version(api: ApiFactory) -> None:
    admin, house = await _admin_with_house(api)
    hid = house["house"]["id"]
    assert (await admin.call("PATCH", f"/api/v1/houses/{hid}", {"name": "4B"})).status_code == 428
    ok = await admin.call("PATCH", f"/api/v1/houses/{hid}", {"name": "4B"}, if_match=1)
    assert ok.json() == {"name": "4B", "version": 2}
    stale = await admin.call("PATCH", f"/api/v1/houses/{hid}", {"name": "Other"}, if_match=1)
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    assert stale.json()["current"] == {"name": "4B", "version": 2}


async def test_idempotent_create_replays_and_mismatch_is_422(
    api: ApiFactory, sessions: async_sessionmaker[AsyncSession]
) -> None:
    admin = api()
    await admin.sign_in("idem@example.com")
    first = await admin.post("/api/v1/houses", {"name": "A"}, key="same-key")
    again = await admin.post("/api/v1/houses", {"name": "A"}, key="same-key")
    assert first.status_code == again.status_code == 201
    assert again.json() == first.json() and again.headers.get("Idempotent-Replayed") == "true"
    mismatch = await admin.post("/api/v1/houses", {"name": "B"}, key="same-key")
    assert mismatch.status_code == 422 and mismatch.json()["code"] == "IDEMPOTENCY_MISMATCH"
    missing = await admin.call("POST", "/api/v1/houses", {"name": "C"}, idem=False)
    assert missing.status_code == 400 and missing.json()["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    async with sessions() as db:
        assert await db.scalar(text("SELECT count(*) FROM houses")) == 1
