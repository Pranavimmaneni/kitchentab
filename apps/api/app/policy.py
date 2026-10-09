"""Every authorization rule lives here and only here (CLAUDE.md rule 2, PRD §7).

Routes and services call `require(action, actor=..., ...)`; the UI only hides buttons. `decide`
is a pure function over small context objects so each row of the PRD §7 table has a direct unit
test (SEC-01, 100% branch coverage). Unknown actions are denied.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from app.errors import ErrorCode, Problem


class Action(StrEnum):
    # Houses and members (Milestone 3)
    HOUSE_READ = "house.read"
    HOUSE_RENAME = "house.rename"
    INVITE_READ = "house.invite.read"
    INVITE_ROTATE = "house.invite.rotate"
    MEMBER_ADD = "member.add"
    MEMBER_RENAME = "member.rename"
    MEMBER_REMOVE = "member.remove"
    MEMBER_UNCLAIM = "member.unclaim"
    ADMIN_TRANSFER = "admin.transfer"
    HOUSE_LEAVE = "house.leave"
    # Bills (Milestone 4; open-bill rules exercised from Milestone 6)
    BILL_CREATE = "bill.create"
    BILL_READ = "bill.read"
    BILL_EDIT = "bill.edit"  # header, items, prices, tax, fees, guests, exclusions
    BILL_SEND = "bill.send"
    BILL_DELETE_DRAFT = "bill.delete_draft"
    ALLOC_SET = "bill.allocation.set"  # tick or untick one participant on one item


@dataclass(frozen=True)
class MemberCtx:
    """The signed-in user's membership in the house being acted on (None if not a member)."""

    member_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    status: str

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def is_admin(self) -> bool:
        return self.active and self.role == "admin"


@dataclass(frozen=True)
class TargetMember:
    member_id: uuid.UUID
    user_id: uuid.UUID | None
    role: str
    status: str


@dataclass(frozen=True)
class BillCtx:
    payer_member_id: uuid.UUID
    created_by_user_id: uuid.UUID
    state: str  # draft | open | settled | voided


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: ErrorCode = ErrorCode.FORBIDDEN
    reason: str = ""


ALLOW = Decision(True)
ADMIN_ONLY = {
    Action.HOUSE_RENAME,
    Action.INVITE_ROTATE,
    Action.MEMBER_ADD,
    Action.MEMBER_RENAME,
    Action.MEMBER_REMOVE,
    Action.MEMBER_UNCLAIM,
    Action.ADMIN_TRANSFER,
}
BILL_ACTIONS = {Action.BILL_READ, Action.BILL_EDIT, Action.BILL_SEND, Action.BILL_DELETE_DRAFT, Action.ALLOC_SET}


def _decide_member_admin(action: Action, actor: MemberCtx, target: TargetMember | None) -> Decision:
    if not actor.is_admin:
        return Decision(False, ErrorCode.ADMIN_REQUIRED, "Only the house admin can do that")
    if target is None:
        return ALLOW
    if target.status == "removed":
        return Decision(False, ErrorCode.NOT_FOUND, "That person isn't in the house")
    if action == Action.MEMBER_REMOVE and target.member_id == actor.member_id:
        return Decision(False, ErrorCode.FORBIDDEN, "Use Leave house to remove yourself")
    if action == Action.MEMBER_UNCLAIM and (target.user_id is None or target.member_id == actor.member_id):
        return Decision(False, ErrorCode.CONFLICT, "Only someone else's claimed name can be unclaimed")
    if action == Action.ADMIN_TRANSFER and (target.status != "active" or target.member_id == actor.member_id):
        return Decision(False, ErrorCode.CONFLICT, "Admin can only go to another member who has joined")
    return ALLOW


def _decide_bill(
    action: Action, actor: MemberCtx, bill: BillCtx | None, participant_member_id: uuid.UUID | None
) -> Decision:
    if bill is None:
        return Decision(False, ErrorCode.FORBIDDEN, "missing bill")
    if action == Action.BILL_READ:
        return ALLOW
    if bill.state == "settled":
        return Decision(False, ErrorCode.BILL_SETTLED, "This tab is settled")
    if bill.state == "voided":
        return Decision(False, ErrorCode.BILL_VOIDED, "This tab was voided")
    is_payer = actor.member_id == bill.payer_member_id
    # While a draft, the uploader keeps editing even after picking someone else as payer.
    is_owner = is_payer or (bill.state == "draft" and actor.user_id == bill.created_by_user_id)
    if action == Action.BILL_SEND and bill.state != "draft":
        return Decision(False, ErrorCode.CONFLICT, "This tab was already sent")
    if action == Action.BILL_DELETE_DRAFT and bill.state != "draft":
        return Decision(False, ErrorCode.CONFLICT, "Sent tabs can be voided, not deleted")
    if action in (Action.BILL_EDIT, Action.BILL_SEND, Action.BILL_DELETE_DRAFT):
        return ALLOW if is_owner else Decision(False, ErrorCode.FORBIDDEN, "Only the payer can change this tab")
    # ALLOC_SET: the payer edits anyone's ticks; on an open bill members edit their own (FR-32, FR-33).
    if is_owner or (bill.state == "open" and participant_member_id == actor.member_id):
        return ALLOW
    return Decision(False, ErrorCode.FORBIDDEN, "You can only change your own ticks")


def decide(
    action: Action,
    *,
    actor: MemberCtx | None,
    target: TargetMember | None = None,
    bill: BillCtx | None = None,
    participant_member_id: uuid.UUID | None = None,
) -> Decision:
    if actor is None or not actor.active:
        return Decision(False, ErrorCode.NOT_A_MEMBER, "You're not in this house")
    if action in (Action.HOUSE_READ, Action.INVITE_READ, Action.HOUSE_LEAVE, Action.BILL_CREATE):
        # Leave: business checks (admin hand-over, open bills as payer) live in the service.
        return ALLOW
    if action in ADMIN_ONLY:
        return _decide_member_admin(action, actor, target)
    if action in BILL_ACTIONS:
        return _decide_bill(action, actor, bill, participant_member_id)
    return Decision(False, ErrorCode.FORBIDDEN, f"no rule for {action!s}")


STATUS_FOR = {
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.BILL_SETTLED: 409,
    ErrorCode.BILL_VOIDED: 409,
}


def require(
    action: Action,
    *,
    actor: MemberCtx | None,
    target: TargetMember | None = None,
    bill: BillCtx | None = None,
    participant_member_id: uuid.UUID | None = None,
) -> None:
    decision = decide(action, actor=actor, target=target, bill=bill, participant_member_id=participant_member_id)
    if not decision.allowed:
        raise Problem(STATUS_FOR.get(decision.code, 403), decision.code, decision.reason or "You can't do that here")
