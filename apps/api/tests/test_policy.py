"""The policy table, cell by cell (PRD §7; TEST_PLAN SEC-01 unit part). `decide` is pure, so every
action × role × state combination is checked directly; the HTTP matrix lives in test_security."""

from __future__ import annotations

import uuid

import pytest

from app.errors import ErrorCode, Problem
from app.policy import (
    ADMIN_ONLY,
    PAYER_ONLY,
    Action,
    AskCtx,
    BillCtx,
    ItemCtx,
    MemberCtx,
    TargetMember,
    decide,
    decide_own,
    require,
    require_own,
)
from tests.harness.tags import tid

ADMIN_U, PAYER_U, MEMBER_U, OTHER_U = (uuid.uuid4() for _ in range(4))
ADMIN = MemberCtx(uuid.uuid4(), ADMIN_U, "admin", "active")
PAYER = MemberCtx(uuid.uuid4(), PAYER_U, "member", "active")
MEMBER = MemberCtx(uuid.uuid4(), MEMBER_U, "member", "active")
LEFT = MemberCtx(uuid.uuid4(), OTHER_U, "member", "left")


def bill(state: str, *, created_by: uuid.UUID = PAYER_U) -> BillCtx:
    return BillCtx(PAYER.member_id, created_by, state)


def target(m: MemberCtx, *, status: str = "active", user: bool = True) -> TargetMember:
    return TargetMember(m.member_id, m.user_id if user else None, m.role, status)


@tid("SEC-01")
@pytest.mark.parametrize("action", list(Action))
def test_SEC_01_non_members_and_departed_members_get_nothing(action: Action) -> None:
    for actor in (None, LEFT):
        d = decide(action, actor=actor, bill=bill("open"), item=ItemCtx(frozenset()), ask=AskCtx(uuid.uuid4()))
        assert not d.allowed and d.code == ErrorCode.NOT_A_MEMBER


@tid("SEC-01")
@pytest.mark.parametrize("action", [Action.HOUSE_READ, Action.INVITE_READ, Action.HOUSE_LEAVE, Action.BILL_CREATE])
def test_SEC_01_any_active_member(action: Action) -> None:
    assert decide(action, actor=MEMBER).allowed and decide(action, actor=ADMIN).allowed


@tid("SEC-01")
@pytest.mark.parametrize("action", sorted(ADMIN_ONLY))
def test_SEC_01_admin_only(action: Action) -> None:
    d = decide(action, actor=MEMBER, target=target(PAYER))
    assert not d.allowed and d.code == ErrorCode.ADMIN_REQUIRED
    assert decide(action, actor=ADMIN).allowed, "no target: the admin may"


@tid("SEC-01")
def test_SEC_01_admin_rules_on_targets() -> None:
    removed = decide(Action.MEMBER_RENAME, actor=ADMIN, target=target(MEMBER, status="removed"))
    assert removed.code == ErrorCode.NOT_FOUND
    assert decide(Action.MEMBER_REMOVE, actor=ADMIN, target=target(ADMIN)).code == ErrorCode.FORBIDDEN
    assert decide(Action.MEMBER_REMOVE, actor=ADMIN, target=target(MEMBER)).allowed
    assert decide(Action.MEMBER_UNCLAIM, actor=ADMIN, target=target(MEMBER, user=False)).code == ErrorCode.CONFLICT
    assert decide(Action.MEMBER_UNCLAIM, actor=ADMIN, target=target(ADMIN)).code == ErrorCode.CONFLICT
    assert decide(Action.MEMBER_UNCLAIM, actor=ADMIN, target=target(MEMBER)).allowed
    invited = decide(Action.ADMIN_TRANSFER, actor=ADMIN, target=target(MEMBER, status="invited"))
    assert invited.code == ErrorCode.CONFLICT
    assert decide(Action.ADMIN_TRANSFER, actor=ADMIN, target=target(ADMIN)).code == ErrorCode.CONFLICT
    assert decide(Action.ADMIN_TRANSFER, actor=ADMIN, target=target(MEMBER)).allowed


@tid("SEC-01")
def test_SEC_01_bill_rules() -> None:
    assert decide(Action.BILL_READ, actor=MEMBER, bill=bill("voided")).allowed
    assert decide(Action.BILL_EDIT, actor=PAYER).code == ErrorCode.FORBIDDEN, "missing bill context"
    assert decide(Action.BILL_EDIT, actor=PAYER, bill=bill("settled")).code == ErrorCode.BILL_SETTLED
    assert decide(Action.BILL_EDIT, actor=PAYER, bill=bill("voided")).code == ErrorCode.BILL_VOIDED
    assert decide(Action.BILL_SEND, actor=PAYER, bill=bill("open")).code == ErrorCode.CONFLICT
    assert decide(Action.BILL_DELETE_DRAFT, actor=PAYER, bill=bill("open")).code == ErrorCode.CONFLICT
    for action in (Action.BILL_EDIT, Action.BILL_SEND, Action.BILL_DELETE_DRAFT):
        assert decide(action, actor=PAYER, bill=bill("draft")).allowed
        assert decide(action, actor=MEMBER, bill=bill("draft")).code == ErrorCode.FORBIDDEN
        assert decide(action, actor=MEMBER, bill=bill("draft", created_by=MEMBER_U)).allowed, "the uploader"
    assert not decide(Action.BILL_EDIT, actor=MEMBER, bill=bill("open", created_by=MEMBER_U)).allowed
    # Ticks: the payer anyone's; on an open bill a member their own column only.
    assert decide(Action.ALLOC_SET, actor=PAYER, bill=bill("open"), participant_member_id=MEMBER.member_id).allowed
    assert decide(Action.ALLOC_SET, actor=MEMBER, bill=bill("open"), participant_member_id=MEMBER.member_id).allowed
    other = decide(Action.ALLOC_SET, actor=MEMBER, bill=bill("open"), participant_member_id=PAYER.member_id)
    assert other.code == ErrorCode.FORBIDDEN
    draft = decide(Action.ALLOC_SET, actor=MEMBER, bill=bill("draft"), participant_member_id=MEMBER.member_id)
    assert not draft.allowed, "a draft is the payer's until sent"


@tid("SEC-01")
@pytest.mark.parametrize("action", sorted(PAYER_ONLY))
def test_SEC_01_payer_only_open_bill_life(action: Action) -> None:
    assert decide(action, actor=PAYER, bill=bill("open")).allowed
    d = decide(action, actor=MEMBER, bill=bill("open"))
    assert not d.allowed and d.reason == PAYER_ONLY[action]
    assert decide(action, actor=ADMIN, bill=bill("open")).code == ErrorCode.FORBIDDEN, "admin isn't the payer"
    assert decide(action, actor=PAYER).code == ErrorCode.FORBIDDEN, "missing bill context"


@tid("SEC-01")
def test_SEC_01_payments_are_recorded_by_the_payer_or_for_yourself() -> None:
    mine = decide(Action.PAYMENT_RECORD, actor=MEMBER, bill=bill("open"), participant_member_id=MEMBER.member_id)
    assert mine.allowed
    theirs = decide(Action.PAYMENT_RECORD, actor=MEMBER, bill=bill("open"), participant_member_id=ADMIN.member_id)
    assert theirs.reason == "You can only record your own payment"
    guest = decide(Action.PAYMENT_RECORD, actor=MEMBER, bill=bill("open"), participant_member_id=None)
    assert not guest.allowed
    assert decide(Action.PAYMENT_RECORD, actor=PAYER, bill=bill("open"), participant_member_id=None).allowed


@tid("SEC-01")
def test_SEC_01_kitchen_owners_and_askers() -> None:
    owners = ItemCtx(frozenset({PAYER.member_id, ADMIN.member_id}))
    assert decide(Action.ITEM_OWNER_ACT, actor=PAYER, item=owners).allowed
    assert decide(Action.ITEM_OWNER_ACT, actor=MEMBER, item=owners).code == ErrorCode.FORBIDDEN
    assert not decide(Action.ITEM_OWNER_ACT, actor=PAYER).allowed, "missing item context"
    assert decide(Action.ASK_CANCEL, actor=MEMBER, ask=AskCtx(MEMBER.member_id)).allowed
    assert decide(Action.ASK_CANCEL, actor=ADMIN, ask=AskCtx(MEMBER.member_id)).code == ErrorCode.FORBIDDEN
    assert not decide(Action.ASK_CANCEL, actor=MEMBER).allowed


@tid("SEC-01", "SEC-10")
def test_SEC_01_own_account_resources() -> None:
    assert decide_own(MEMBER_U, MEMBER_U).allowed
    assert decide_own(ADMIN_U, MEMBER_U).code == ErrorCode.FORBIDDEN
    assert decide_own(None, MEMBER_U).code == ErrorCode.NOT_FOUND
    with pytest.raises(Problem) as forbidden:
        require_own(ADMIN_U, MEMBER_U)
    assert forbidden.value.status == 403
    with pytest.raises(Problem) as missing:
        require_own(None, MEMBER_U)
    assert missing.value.status == 404


@tid("SEC-01")
def test_SEC_01_unknown_actions_are_denied_and_require_maps_status() -> None:
    class Fake:
        pass

    assert not decide(Fake(), actor=MEMBER).allowed  # type: ignore[arg-type]
    with pytest.raises(Problem) as settled:
        require(Action.BILL_EDIT, actor=PAYER, bill=bill("settled"))
    assert settled.value.status == 409
    with pytest.raises(Problem) as gone:
        require(Action.MEMBER_RENAME, actor=ADMIN, target=target(MEMBER, status="removed"))
    assert gone.value.status == 404
    with pytest.raises(Problem) as outsider:
        require(Action.HOUSE_READ, actor=None)
    assert outsider.value.status == 403


@tid("SEC-01")
def test_SEC_01_require_returns_quietly_when_allowed() -> None:
    require(Action.HOUSE_READ, actor=MEMBER)  # no exception
    require_own(MEMBER_U, MEMBER_U)
