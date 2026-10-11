"""Test ID and FR tagging (TEST_PLAN intro; read by scripts/check_traceability.py).

@tid("SYNC-25")
@fr("FR-62")
async def test_SYNC_25_gapless_sequence(): ...
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, TypeVar

import pytest

F = TypeVar("F", bound=Callable[..., Any])

# Feature specs number their own rows with a G (kitchen gestures: SYNC-G1, FR-G01).
TEST_ID = re.compile(r"^(?:[A-Z0-9]+-(?:\d{2}|G\d)|EDGE-(?:15|19)-\d{2})$")
FR_ID = re.compile(r"^FR-G?\d{2}$")


def tid(*ids: str) -> Callable[[F], F]:
    for i in ids:
        if not TEST_ID.match(i):
            raise ValueError(f"not a TEST_PLAN id: {i}")
    mark: Callable[[F], F] = pytest.mark.id(*ids)
    return mark


def fr(*ids: str) -> Callable[[F], F]:
    for i in ids:
        if not FR_ID.match(i):
            raise ValueError(f"not an FR id: {i}")
    mark: Callable[[F], F] = pytest.mark.fr(*ids)
    return mark
