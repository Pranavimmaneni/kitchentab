"""Write a scenario into the database as raw rows.

The seeder is a snapshot loader, not a replay of user actions: it bypasses the services, writes no
house events, and leaves `houses.event_seq` at 0. Derived rows (share snapshots, activity) appear
as the milestones that own them land. Never runs in staging or prod.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.kitchen import default_shelf_life
from app.links import bill_token, invite_token
from app.models import (
    Allocation,
    AuthIdentity,
    Base,
    Bill,
    House,
    InventoryGrant,
    InventoryItem,
    InventoryOwner,
    Item,
    Member,
    Payment,
    ShareRequest,
    ShareSnapshot,
    User,
)
from app.seed.scenario import Scenario, seed_uuid


async def truncate_all(session: AsyncSession) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    await session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


async def write_scenario(session: AsyncSession, sc: Scenario, pepper: str) -> dict[str, uuid.UUID]:
    """Insert every row of `sc`; returns a map like {"member:pranav": id, "bill:shoprite": id}."""
    raw = sc.raw
    ids: dict[str, uuid.UUID] = {}
    house_created = datetime.fromisoformat(str(raw["house"]["created_at"]))

    def uid(kind: str, key: str, at: datetime) -> uuid.UUID:
        ids[f"{kind}:{key}"] = seed_uuid(sc.name, kind, key, at)
        return ids[f"{kind}:{key}"]

    identities: list[AuthIdentity] = []
    for u in raw["users"]:
        user = User(
            id=uid("user", u["key"], house_created),
            display_name=u["name"],
            email=u.get("email"),
            email_verified_at=house_created if u.get("email") else None,
            phone_e164=u.get("phone"),
            phone_verified_at=house_created if u.get("phone") else None,
            created_at=house_created,
        )
        subject = u.get("email") or u.get("phone")
        session.add(user)
        identities.append(
            AuthIdentity(
                id=uid("identity", u["key"], house_created),
                user_id=user.id,
                provider=u["via"],
                provider_subject=str(subject),
                created_at=house_created,
            )
        )
    await session.flush()
    session.add_all(identities)
    await session.flush()

    admin_user = ids[f"user:{raw['house']['admin']}"]
    house_id = uid("house", sc.name, house_created)
    house = House(
        id=house_id,
        name=raw["house"]["name"],
        timezone=raw["house"]["timezone"],
        invite_token_hash=invite_token(pepper, house_id, house_created)[1],
        created_by=admin_user,
        created_at=house_created,
    )
    session.add(house)
    await session.flush()

    members = []
    for order, m in enumerate(raw["members"]):
        members.append(
            Member(
                id=uid("member", m["key"], house_created),
                house_id=house.id,
                user_id=ids[f"user:{m['user']}"],
                display_name=m["name"],
                color=m["color"],
                sort_order=order,
                role=m.get("role", "member"),
                status="active",
                created_at=house_created,
                joined_at=house_created,
            )
        )
    session.add_all(members)
    await session.flush()

    inventory_specs = raw.get("kitchen", {})
    for bill in sc.bills:
        created = bill.sent_at or sc.now
        payer = ids[f"member:{bill.payer}"]
        subtotal = sum(i.cents for i in bill.items)
        bill_id = uid("bill", bill.key, created)
        b = Bill(
            id=bill_id,
            house_id=house.id,
            payer_member_id=payer,
            created_by=ids[f"user:{bill.payer}"],
            store_name=bill.store,
            purchase_date=bill.purchase_date,
            subtotal_cents=subtotal,
            tax_cents=bill.tax_cents,
            total_cents=bill.total_cents,
            state=bill.state,
            link_token_hash=bill_token(pepper, bill_id, created)[1],
            sent_at=bill.sent_at,
            settled_at=bill.settled_at,
            settled_by=payer if bill.settled_at else None,
            created_at=created,
            updated_at=bill.settled_at or created,
        )
        session.add(b)
        await session.flush()
        for pos, it in enumerate(bill.items):
            item = Item(
                id=uid("item", f"{bill.key}.{it.key}", created),
                bill_id=b.id,
                position=pos,
                raw_text=it.name.upper(),
                name=it.name,
                quantity=it.quantity,
                unit_price_cents=it.unit_price_cents,
                line_total_cents=it.cents,
                taxable=it.storage != "none",
                confidence=it.confidence,
                flagged=it.flagged,
                storage=it.storage,
                category=it.category,
                icon_key=it.icon,
                shelf_life_days=it.shelf_life_days
                or (None if it.storage == "none" else default_shelf_life(it.category, it.icon)),
                created_at=created,
                updated_at=created,
            )
            session.add(item)
            await session.flush()
            session.add_all(
                [
                    Allocation(
                        id=uid("allocation", f"{bill.key}.{it.key}.{who}", created),
                        item_id=item.id,
                        member_id=ids[f"member:{who}"],
                        created_by=ids[f"user:{bill.payer}"],
                        created_at=created,
                        updated_by_member_id=payer,
                        updated_at=created,
                    )
                    for who in it.who
                ]
            )
            if it.storage != "none":
                spec = inventory_specs.get(f"{bill.key}.{it.key}", {})
                await _stock(
                    session,
                    sc,
                    ids,
                    f"{bill.key}.{it.key}",
                    b,
                    item,
                    it.who,
                    spec,
                    it.used_up_by,
                    it.used_up_at,
                )
        for n, p in enumerate(bill.payments):
            session.add(
                Payment(
                    id=uid("payment", f"{bill.key}.{n}", p.at),
                    bill_id=b.id,
                    member_id=ids[f"member:{p.member}"],
                    to_member_id=payer,
                    direction="to_payer",
                    amount_cents=p.amount_cents,
                    method=p.method,
                    status=p.status,
                    recorded_by=ids[f"user:{p.member}"],
                    confirmed_by=ids[f"user:{bill.payer}"] if p.status == "confirmed" else None,
                    created_at=p.at,
                    confirmed_at=p.at + timedelta(hours=1) if p.status == "confirmed" else None,
                )
            )
        await session.flush()
        if bill.state != "draft":  # what send would have stored (FR-30): one snapshot per participant
            from app.services import bill_math

            for key, cents in bill_math.shares(await bill_math.load(session, b)).shares.items():
                session.add(
                    ShareSnapshot(
                        id=uid("snapshot", f"{bill.key}.{key}", created),
                        bill_id=b.id,
                        member_id=uuid.UUID(key[2:]) if key.startswith("m:") else None,
                        guest_id=uuid.UUID(key[2:]) if key.startswith("g:") else None,
                        share_cents=cents,
                        bill_version=b.version or 1,
                        computed_at=created,
                        reason="sent",
                    )
                )
            await session.flush()

    await _requests(session, sc, ids, house.id)
    return ids


async def _stock(
    session: AsyncSession,
    sc: Scenario,
    ids: dict[str, uuid.UUID],
    key: str,
    bill: Bill,
    item: Item,
    owners: tuple[str, ...],
    spec: dict[str, Any],
    used_up_by: str | None,
    used_up_at: datetime | None,
) -> None:
    stocked_on = date.fromisoformat(str(spec["stocked_on"])) if spec.get("stocked_on") else bill.purchase_date
    created = bill.sent_at or sc.now
    inv = InventoryItem(
        id=seed_uuid(sc.name, "inventory", key, created),
        house_id=bill.house_id,
        bill_id=bill.id,
        item_id=item.id,
        place=item.storage,
        category=item.category,
        icon_key=item.icon_key or ("generic_fridge" if item.storage in ("fridge", "freezer") else "generic_pantry"),
        name=item.name,
        short_name=spec.get("short", item.name[:14]),
        price_cents=item.line_total_cents,
        stocked_on=stocked_on,
        shelf_life_days=int(spec.get("shelf_life_days") or item.shelf_life_days or 30),
        status="used_up" if used_up_by else "in_stock",
        used_up_by_member_id=ids[f"member:{used_up_by}"] if used_up_by else None,
        used_up_at=used_up_at,
        created_by=bill.payer_member_id,
        created_at=created,
        updated_at=used_up_at or created,
    )
    session.add(inv)
    await session.flush()
    ids[f"inventory:{key}"] = inv.id
    open_owners = set(spec.get("open", []))
    session.add_all(
        [
            InventoryOwner(
                inventory_item_id=inv.id,
                member_id=ids[f"member:{o}"],
                open_to_share=o in open_owners,
                updated_by_member_id=ids[f"member:{o}"],
                updated_at=created,
            )
            for o in owners
        ]
    )


async def _requests(session: AsyncSession, sc: Scenario, ids: dict[str, uuid.UUID], house_id: uuid.UUID) -> None:
    for r in sc.raw.get("requests", []):
        created = sc.now - timedelta(minutes=int(r["minutes_ago"]))
        answered = sc.now - timedelta(minutes=int(r["answered_minutes_ago"])) if r.get("answered_minutes_ago") else None
        inventory_id = ids[f"inventory:{r['item']}"]
        req = ShareRequest(
            id=seed_uuid(sc.name, "request", r["key"], created),
            house_id=house_id,
            inventory_item_id=inventory_id,
            requester_member_id=ids[f"member:{r['requester']}"],
            amount=r["amount"],
            note=r.get("note"),
            status=r["status"],
            resolution=r.get("resolution"),
            answered_by_member_id=ids[f"member:{r['answered_by']}"] if r.get("answered_by") else None,
            answered_at=answered,
            undo_until=answered + timedelta(minutes=5) if answered else None,
            expires_at=created + timedelta(hours=48),
            created_at=created,
        )
        session.add(req)
        await session.flush()
        ids[f"request:{r['key']}"] = req.id
        if r["status"] == "approved" and r.get("resolution") == "free" and answered is not None:
            session.add(
                InventoryGrant(
                    inventory_item_id=inventory_id,
                    member_id=req.requester_member_id,
                    share_request_id=req.id,
                    granted_by_member_id=ids[f"member:{r['answered_by']}"],
                    created_at=answered,
                )
            )
    await session.flush()
