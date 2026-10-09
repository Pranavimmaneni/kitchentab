"""Every authorization rule lives here and only here (CLAUDE.md rule 2, PRD §7).

Routes and services call `require(action, actor=..., target=...)`; the UI only hides buttons.
`decide` is a pure function over small context objects so each row of the PRD §7 table has a
direct unit test (SEC-01, 100% branch coverage). Unknown actions are denied.
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


def decide(action: Action, *, actor: MemberCtx | None, target: TargetMember | None = None) -> Decision:
    if actor is None or not actor.active:
        return Decision(False, ErrorCode.NOT_A_MEMBER, "You're not in this house")
    if action in (Action.HOUSE_READ, Action.INVITE_READ):
        return ALLOW
    if action == Action.HOUSE_LEAVE:
        return ALLOW  # business checks (admin transfer, open bills as payer) live in the service
    if action in ADMIN_ONLY:
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
    return Decision(False, ErrorCode.FORBIDDEN, f"no rule for {action!s}")


def require(action: Action, *, actor: MemberCtx | None, target: TargetMember | None = None) -> None:
    decision = decide(action, actor=actor, target=target)
    if not decision.allowed:
        status = 404 if decision.code == ErrorCode.NOT_FOUND else 409 if decision.code == ErrorCode.CONFLICT else 403
        raise Problem(status, decision.code, decision.reason or "You can't do that here")
