"""VercelBlobStorage maps the storage interface onto Vercel's Python SDK (ADR-0024). The SDK client is
replaced by a stand-in: no network, no real Blob store (TEST_PLAN §2)."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from vercel.blob import BlobNotFoundError

from app.adapters.storage import BLOB_UPLOAD_ROUTE, BLOB_VIEW_ROUTE, VercelBlobStorage
from tests.harness.tags import fr, tid

TOKEN = "vercel_blob_rw_store123_secretpart"


@dataclass
class Meta:
    size: int
    content_type: str
    content: bytes = b""


@dataclass
class FakeSdk:
    blobs: dict[str, Meta] = field(default_factory=dict)
    calls: list[tuple[str, Any]] = field(default_factory=list)

    async def head(self, key: str) -> Meta:
        self.calls.append(("head", key))
        if key not in self.blobs:
            raise BlobNotFoundError()
        return self.blobs[key]

    async def get(self, key: str, *, access: str, use_cache: bool) -> Meta | None:
        self.calls.append(("get", (key, access, use_cache)))
        return self.blobs.get(key)

    async def put(self, key: str, data: bytes, *, access: str, content_type: str, overwrite: bool) -> None:
        self.calls.append(("put", (key, access, content_type, overwrite)))
        self.blobs[key] = Meta(len(data), content_type, data)

    async def delete(self, key: str) -> None:
        self.calls.append(("delete", key))
        self.blobs.pop(key, None)


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> tuple[VercelBlobStorage, FakeSdk]:
    sdk = FakeSdk()
    s = VercelBlobStorage(TOKEN)
    monkeypatch.setattr(s, "_client", lambda: sdk)
    return s, sdk


@tid("EXTRACT-10", "SEC-04")
@fr("FR-10")
async def test_private_store_round_trip_and_signed_view(store: tuple[VercelBlobStorage, FakeSdk]) -> None:
    s, sdk = store
    key = "receipts/bill/abc.pdf"
    assert await s.head(key) is None
    await s.put(key, b"%PDF-1.7 data", "application/pdf")
    assert ("put", (key, "private", "application/pdf", True)) in sdk.calls, "receipts are never public"
    info = await s.head(key)
    assert info is not None and (info.size, info.content_type) == (13, "application/pdf")
    assert await s.get(key) == b"%PDF-1.7 data" and await s.get(key, first_bytes=4) == b"%PDF"
    assert ("get", (key, "private", False)) in sdk.calls
    url = await s.view_url(key)
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert parts.path == BLOB_VIEW_ROUTE and not parts.netloc, "same-origin route that mints the signed URL"
    expected = hmac.new(TOKEN.encode(), f"GET\n{key}\n{q['expires']}".encode(), hashlib.sha256).hexdigest()
    assert q["pathname"] == key and q["sig"] == expected
    await s.delete(key)
    assert await s.head(key) is None


@tid("EXTRACT-01")
@fr("FR-10")
async def test_upload_ticket_is_a_client_upload_through_the_web_route(
    store: tuple[VercelBlobStorage, FakeSdk],
) -> None:
    s, _ = store
    ticket = (await s.upload_ticket("receipts/b/x.jpg", "image/jpeg", 20 * 1024 * 1024)).wire()
    assert ticket["method"] == "blob-client" and ticket["upload_url"] is None
    assert ticket["blob"]["handle_upload_url"] == BLOB_UPLOAD_ROUTE
    assert ticket["blob"]["allowed_content_types"] == ["image/jpeg"]
    assert ticket["blob"]["maximum_size_in_bytes"] == 20 * 1024 * 1024
    assert ticket["blob"]["client_token"] is None, "the API never mints production client tokens"
