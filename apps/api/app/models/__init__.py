"""All tables. Importing this package registers every model on `Base.metadata`."""

from app.models.base import Base
from app.models.bills import (
    Allocation,
    Bill,
    BillExclusion,
    BillFee,
    Guest,
    Item,
    LedgerEntry,
    Payment,
    Receipt,
    ReceiptExtraction,
    SeenMarker,
    ShareSnapshot,
)
from app.models.house import House, Member
from app.models.identity import AuthIdentity, OtpCode, PaymentHandle, Session, User
from app.models.kitchen import InventoryGrant, InventoryItem, InventoryOwner, MemberItemOrder, ShareRequest
from app.models.notify import Notification, NotificationPref, PushSubscription
from app.models.platform import RateLimitHit, TestInboxMessage
from app.models.sync import ActivityEvent, HouseEvent, IdempotencyKey, OutboxRow

__all__ = [
    "ActivityEvent",
    "Allocation",
    "AuthIdentity",
    "Base",
    "Bill",
    "BillExclusion",
    "BillFee",
    "Guest",
    "House",
    "HouseEvent",
    "IdempotencyKey",
    "InventoryGrant",
    "InventoryItem",
    "InventoryOwner",
    "Item",
    "LedgerEntry",
    "Member",
    "MemberItemOrder",
    "Notification",
    "NotificationPref",
    "OtpCode",
    "OutboxRow",
    "Payment",
    "PaymentHandle",
    "PushSubscription",
    "RateLimitHit",
    "Receipt",
    "ReceiptExtraction",
    "SeenMarker",
    "Session",
    "ShareRequest",
    "ShareSnapshot",
    "TestInboxMessage",
    "User",
]
