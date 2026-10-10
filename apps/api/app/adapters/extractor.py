"""Receipt extraction behind one interface (PRD §9). The vision-LLM adapter lands in Milestone 5."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol

from redis.asyncio import Redis

FAKE_EXTRACTOR_MODE_KEY = "test:extractor:mode"
FakeMode = Literal["normal", "fail", "hang"]


class ExtractionFailed(Exception):
    pass


class ReceiptExtractor(Protocol):
    model: str
    prompt_version: str

    async def extract(self, images: list[bytes]) -> dict[str, Any]: ...


def image_key(images: list[bytes]) -> str:
    h = hashlib.sha256()
    for img in images:
        h.update(hashlib.sha256(img).digest())
    return h.hexdigest()


class FakeExtractor:
    """Returns fixture JSON chosen by the images' hash (TEST_PLAN §2).

    Fixtures live in `fixtures_dir/<sha256>.json`. The mode is read from Redis on every call so a
    test can switch it from another process: `normal`, `fail` (raises) or `hang` (sleeps past the
    caller's 30 s timeout, CHAOS-07).
    """

    model = "fake-extractor"
    prompt_version = "fake-1"

    def __init__(self, fixtures_dir: Path, redis: Redis | None = None, hang_seconds: float = 3600) -> None:
        self._dir = fixtures_dir
        self._redis = redis
        self._hang = hang_seconds
        self._mode: FakeMode = "normal"

    async def set_mode(self, mode: FakeMode) -> None:
        self._mode = mode
        if self._redis is not None:
            await self._redis.set(FAKE_EXTRACTOR_MODE_KEY, mode)

    async def _current_mode(self) -> str:
        if self._redis is not None:
            stored = await self._redis.get(FAKE_EXTRACTOR_MODE_KEY)
            if stored:
                return str(stored)
        return self._mode

    async def extract(self, images: list[bytes]) -> dict[str, Any]:
        mode = await self._current_mode()
        if mode == "fail":
            raise ExtractionFailed("fake extractor told to fail")
        if mode == "hang":
            await asyncio.sleep(self._hang)
        path = self._dir / f"{image_key(images)}.json"
        if not path.exists():
            raise ExtractionFailed("no fixture for these images")
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data


# ---- the optional plugin (BUILD_ALL C1, ADR-0010) -------------------------------------------------

PLUGIN_TIMEOUT_SECONDS = 30  # PRD §9: "temperature 0, timeout 30 s"
FAKE_EXTRACTOR_FIXTURES = Path(__file__).resolve().parents[4] / "packages" / "fixtures" / "extractor"


def plugin_enabled(settings: Any) -> bool:
    """The vision-model plugin runs only when EXTRACTOR_API_KEY is set (EXTRACT-15); with no key every
    receipt goes through the local pipeline (text PDF parser or browser OCR)."""
    return settings.extractor_provider != "local" and settings.extractor_api_key is not None


def build_plugin(settings: Any, redis: Redis | None = None) -> ReceiptExtractor | None:
    """`fake` → FakeExtractor (tests). `llm` has no vendor adapter in this build, so it is reported
    once and the local pipeline is used (never a silent failure of every photo)."""
    if not plugin_enabled(settings):
        return None
    if settings.extractor_provider == "fake":
        return FakeExtractor(FAKE_EXTRACTOR_FIXTURES, redis)
    import logging

    logging.getLogger(__name__).warning("EXTRACTOR_PROVIDER=llm has no adapter yet; using local extraction")
    return None
