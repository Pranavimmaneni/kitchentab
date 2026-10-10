"""Allowed values for every enum column. Single source for models, migrations and API schemas."""

from __future__ import annotations

THEMES = ("system", "light", "dark")
AUTH_PROVIDERS = ("google", "email", "phone")
OTP_CHANNELS = ("email", "phone")

MEMBER_ROLES = ("admin", "member")
MEMBER_STATUSES = ("placeholder", "active", "left", "removed")
PAYMENT_HANDLE_METHODS = ("venmo", "cashapp", "paypal", "zelle")

BILL_STATES = ("draft", "open", "settled", "voided")
EXTRACTION_STATUSES = ("none", "pending", "running", "done", "failed")
BILL_FEE_KINDS = ("fee", "tip", "order_discount", "adjustment")
RECEIPT_MIMES = ("image/jpeg", "image/png", "image/heic", "image/webp", "application/pdf")
STORAGE_TAGS = ("fridge", "freezer", "pantry", "none")
CATEGORIES = (
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
)
PAYMENT_METHODS = ("venmo", "cashapp", "paypal", "zelle", "cash", "other")
PAYMENT_DIRECTIONS = ("to_payer", "refund")
PAYMENT_STATUSES = ("pending_confirmation", "confirmed", "rejected")
LEDGER_SOURCES = ("carryover", "settle_up", "chip_in", "void_credit")

PLACES = ("fridge", "freezer", "pantry")
INVENTORY_STATUSES = ("in_stock", "used_up", "removed")
ASK_AMOUNTS = ("a_bit", "half", "all")
REQUEST_STATUSES = ("pending", "approved", "declined", "expired", "cancelled")
REQUEST_RESOLUTIONS = ("free", "split", "chip_in")

NOTIFICATION_CHANNELS = ("inapp", "push", "email")
PREF_CHANNELS = ("push", "email")
NOTIFICATION_TYPES = (
    "asks",
    "answers",
    "share",
    "tab",
    "pay",
    "remind",
    "soon",
    "settled",
    "joined",
    "digest",
    "test",
)
NOTIFICATION_STATUSES = ("pending", "held", "sent", "failed", "suppressed")
OUTBOX_STATUSES = ("pending", "processing", "done", "failed")

# Limits (PRD §6 edge rules).
MAX_MEMBERS_PER_HOUSE = 20
MAX_ITEMS_PER_BILL = 200
MAX_BILL_TOTAL_CENTS = 10_000_000
