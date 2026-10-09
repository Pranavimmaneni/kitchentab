"""Receipt extraction tests: pure functions over fixtures, no database (BUILD_ALL C1)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.extraction.evaluate import FIXTURES
from app.extraction.schema import ExtractedBill


def manifest() -> list[dict[str, Any]]:
    data: dict[str, Any] = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    fixtures: list[dict[str, Any]] = data["fixtures"]
    return fixtures


def truth(fid: str) -> ExtractedBill:
    return ExtractedBill.model_validate_json((FIXTURES / f"{fid}.truth.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def fixtures_by_id() -> dict[str, dict[str, Any]]:
    return {fx["id"]: fx for fx in manifest()}
