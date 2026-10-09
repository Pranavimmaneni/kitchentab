"""Scenario files (`tests/scenarios/*.yaml`): parsing and deterministic ids."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.models import enums
from app.settings import REPO_ROOT

SCENARIO_DIR = REPO_ROOT / "tests" / "scenarios"


def seed_uuid(scenario: str, kind: str, key: str, at: datetime) -> uuid.UUID:
    """Deterministic UUIDv7: timestamp from `at`, random bits from the scenario/kind/key hash,
    so e2e tests and manual QA can rely on stable ids across reseeds."""
    ms = int(at.timestamp() * 1000) & ((1 << 48) - 1)
    digest = int.from_bytes(hashlib.sha256(f"{scenario}:{kind}:{key}".encode()).digest()[:10], "big")
    rand_a = digest >> 68 & 0xFFF
    rand_b = digest & ((1 << 62) - 1)
    return uuid.UUID(int=(ms << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b)


def _dt(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError(f"scenario timestamps need a UTC offset: {value}")
    return parsed


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


@dataclass(frozen=True)
class ItemSpec:
    key: str
    name: str
    cents: int
    storage: str
    category: str | None
    icon: str | None
    who: tuple[str, ...]
    quantity: Decimal
    unit_price_cents: int | None
    flagged: bool
    confidence: Decimal | None
    shelf_life_days: int | None
    used_up_by: str | None
    used_up_at: datetime | None


@dataclass(frozen=True)
class PaymentSpec:
    member: str
    amount_cents: int
    method: str
    status: str
    at: datetime


@dataclass(frozen=True)
class BillSpec:
    key: str
    store: str
    purchase_date: date
    payer: str
    state: str
    sent_at: datetime | None
    settled_at: datetime | None
    tax_cents: int
    total_cents: int
    items: tuple[ItemSpec, ...]
    payments: tuple[PaymentSpec, ...]
    expected_shares: dict[str, int]


@dataclass(frozen=True)
class Scenario:
    name: str
    now: datetime
    raw: dict[str, Any]
    bills: tuple[BillSpec, ...]

    @property
    def member_keys(self) -> list[str]:
        return [m["key"] for m in self.raw["members"]]


def _item(raw: dict[str, Any]) -> ItemSpec:
    if raw["storage"] not in enums.STORAGE_TAGS:
        raise ValueError(f"bad storage for {raw['key']}: {raw['storage']}")
    if raw.get("category") is not None and raw["category"] not in enums.CATEGORIES:
        raise ValueError(f"bad category for {raw['key']}: {raw['category']}")
    used = raw.get("used_up") or {}
    return ItemSpec(
        key=raw["key"],
        name=raw["name"],
        cents=int(raw["cents"]),
        storage=raw["storage"],
        category=raw.get("category"),
        icon=raw.get("icon"),
        who=tuple(raw["who"]),
        quantity=Decimal(str(raw.get("quantity", "1"))),
        unit_price_cents=raw.get("unit_price_cents", raw["cents"]),
        flagged=bool(raw.get("flagged", False)),
        confidence=Decimal(str(raw["confidence"])) if raw.get("confidence") else None,
        shelf_life_days=raw.get("shelf_life_days"),
        used_up_by=used.get("by"),
        used_up_at=_dt(used["at"]) if used.get("at") else None,
    )


def load_scenario(name: str, directory: Path = SCENARIO_DIR) -> Scenario:
    path = directory / f"{name}.yaml"
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    members = {m["key"] for m in raw["members"]}
    bills = []
    for b in raw["bills"]:
        items = tuple(_item(i) for i in b["items"])
        for it in items:
            unknown = set(it.who) - members
            if unknown:
                raise ValueError(f"{b['key']}.{it.key}: unknown members {unknown}")
        bills.append(
            BillSpec(
                key=b["key"],
                store=b["store"],
                purchase_date=_date(b["purchase_date"]),
                payer=b["payer"],
                state=b["state"],
                sent_at=_dt(b["sent_at"]) if b.get("sent_at") else None,
                settled_at=_dt(b["settled_at"]) if b.get("settled_at") else None,
                tax_cents=int(b["tax_cents"]),
                total_cents=int(b["total_cents"]),
                items=items,
                payments=tuple(
                    PaymentSpec(p["from"], int(p["amount_cents"]), p["method"], p["status"], _dt(p["at"]))
                    for p in b.get("payments", [])
                ),
                expected_shares={k: int(v) for k, v in b["expected_shares"].items()},
            )
        )
    return Scenario(name=raw["scenario"], now=_dt(raw["now"]), raw=raw, bills=tuple(bills))
