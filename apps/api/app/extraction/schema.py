"""`ExtractedBill`: the one output shape of every extractor (PRD §9 + §19.7).

The local parser and the optional LLM plugin both return this. All money is integer cents;
`discount_cents` and fee `amount_cents` are positive amounts (a discount reduces the line).
Refunds/returns are items with a negative `line_total_cents`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import CATEGORIES, STORAGE_TAGS

Storage = Literal["fridge", "freezer", "pantry", "none"]
Category = Literal[
    "produce",
    "dairy",
    "eggs",
    "meat_fish",
    "plant_protein",
    "drinks",
    "frozen",
    "grains",
    "bakery",
    "snacks",
    "condiments",
    "canned",
    "coffee_tea",
    "prepared",
    "household",
    "other",
]
FeeKind = Literal["fee", "tip", "order_discount"]
ExtractionStatus = Literal["done", "low_confidence", "failed"]
ExtractionSource = Literal["pdf_text", "ocr", "llm", "manual"]

FLAG_BELOW = 800  # PRD §9 step 5: confidence < 0.8 (800 thousandths) is flagged "CHECK?"
MAX_ITEMS = 200  # enums.MAX_ITEMS_PER_BILL

assert set(STORAGE_TAGS) == set(Storage.__args__)  # type: ignore[attr-defined]
assert set(CATEGORIES) == set(Category.__args__)  # type: ignore[attr-defined]


class ExtractedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    raw_text: str = Field(default="", max_length=2000)
    quantity: Decimal = Field(default=Decimal(1), gt=0, max_digits=10, decimal_places=3)
    unit_price_cents: int | None = None
    line_total_cents: int  # negative for a refund/return line
    discount_cents: int = Field(default=0, ge=0)
    taxable: bool = True
    confidence_x1000: int = Field(default=1000, ge=0, le=1000)
    flagged: bool = False
    storage: Storage = "pantry"
    category: Category = "other"
    icon_key: str = "generic_pantry"
    shelf_life_days: int | None = Field(default=None, ge=0, le=3650)

    @model_validator(mode="after")
    def _flag(self) -> ExtractedItem:
        if self.confidence_x1000 < FLAG_BELOW:
            self.flagged = True
        if self.category == "household":
            self.storage = "none"
        return self

    @property
    def net_cents(self) -> int:
        return self.line_total_cents - self.discount_cents


class ExtractedFee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: FeeKind
    label: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(ge=0)
    confidence_x1000: int = Field(default=1000, ge=0, le=1000)


class ExtractedBill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    store_name: str | None = None
    purchase_date: date | None = None
    currency: Literal["USD"] = "USD"
    items: list[ExtractedItem] = Field(default_factory=list, max_length=MAX_ITEMS)
    subtotal_cents: int | None = None
    tax_cents: int | None = None
    fees: list[ExtractedFee] = Field(default_factory=list)
    total_cents: int | None = None  # as printed (never trusted)
    computed_total_cents: int = 0  # server arithmetic: Σ net lines + tax + fees + tips − order discounts
    mismatch_cents: int | None = None  # printed − computed, when a total was printed and they differ
    field_confidence_x1000: dict[str, int] = Field(default_factory=dict)  # store_name, purchase_date, total
    warnings: list[str] = Field(default_factory=list)
    status: ExtractionStatus = "done"
    source: ExtractionSource = "pdf_text"

    @field_validator("field_confidence_x1000")
    @classmethod
    def _conf_range(cls, v: dict[str, int]) -> dict[str, int]:
        for key, val in v.items():
            if not 0 <= val <= 1000:
                raise ValueError(f"field_confidence_x1000[{key}] out of range")
        return v

    def recompute(self) -> ExtractedBill:
        """Server-side arithmetic (FR-13, EXTRACT-04): never trust printed or model totals."""
        items = sum(i.net_cents for i in self.items)
        fees = sum(f.amount_cents for f in self.fees if f.kind != "order_discount")
        discounts = sum(f.amount_cents for f in self.fees if f.kind == "order_discount")
        self.computed_total_cents = items + (self.tax_cents or 0) + fees - discounts
        self.warnings = [w for w in self.warnings if not w.startswith(("total_mismatch", "subtotal_mismatch"))]
        self.mismatch_cents = None
        if self.total_cents is not None and self.total_cents != self.computed_total_cents:
            self.mismatch_cents = self.total_cents - self.computed_total_cents
            self.warnings.append(
                f"total_mismatch: printed {self.total_cents} computed {self.computed_total_cents} "
                f"difference {self.mismatch_cents}"
            )
        if self.subtotal_cents is not None and self.subtotal_cents != items:
            self.warnings.append(f"subtotal_mismatch: printed {self.subtotal_cents} computed {items}")
        return self

    @property
    def has_mismatch(self) -> bool:
        return self.mismatch_cents is not None or self.total_cents is None
