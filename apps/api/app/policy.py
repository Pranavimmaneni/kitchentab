"""Every authorization rule lives here and only here (CLAUDE.md rule 2, PRD §7).

Routes call `require(...)`; the UI only hides buttons. Rules land with their milestones
(Milestone 3: houses and members; 4: bills; 6: payments and state changes; 8: kitchen) and each
row of the PRD §7 authorization table gets a test (SEC-01, 100% branch coverage).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.errors import ErrorCode, Problem


class Action(StrEnum):
    """Every guarded action. Rules are added per milestone; unknown actions are denied."""


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""


def decide(action: Action, *, actor: object, resource: object) -> Decision:
    """Pure decision function. Default deny until a rule for `action` exists."""
    return Decision(False, f"no rule for {action!s}")


def require(action: Action, *, actor: object, resource: object) -> None:
    decision = decide(action, actor=actor, resource=resource)
    if not decision.allowed:
        raise Problem(403, ErrorCode.FORBIDDEN, "You can't do that here", decision.reason)
