"""Self-test of the fake Vercel Blob service (TEST_PLAN §2) through the API's real Blob adapter."""

from __future__ import annotations

import time
from collections.abc import Iterator

import httpx
import pytest

from app.adapters.storage import FAKE_BLOB_DEFAULT_TOKEN, FakeBlobStorage, mint_client_token, sign_read
from fakeservices import fake_blob
from tests.harness.servers import free_port, serve
from tests.harness.tags import fr, tid

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60


@pytest.fixture(scope="module")
def blob_url() -> Iterator[str]:
    port = free_port()
    with serve(fake_blob.create_app(f"http://127.0.0.1:{port}"), port) as url:
        yield url


@pytest.fixture
def store(blob_url: str) -> FakeBlobStorage:
    httpx.delete(f"{blob_url}/__test/blobs")
    return FakeBlobStorage(blob_url)


def _token(pathname: str, **over: object) -> str:
    payload: dict[str, object] = {
        "pathname": pathname,
        "allowedContentTypes": ["image/jpeg"],
        "maximumSizeInBytes": 100,
        "validUntil": int(time.time() * 1000) + 60_000,
        "addRandomSuffix": False,
    }
    payload.update(over)
    return mint_client_token(FAKE_BLOB_DEFAULT_TOKEN, payload)


def _put(base: str, pathname: str, token: str, data: bytes, ctype: str = "image/jpeg") -> httpx.Response:
    return httpx.put(
        f"{base}/",
        params={"pathname": pathname},
        content=data,
        headers={"authorization": f"Bearer {token}", "x-content-type": ctype},
    )


def test_default_token_matches_the_fake() -> None:
    assert fake_blob.DEFAULT_TOKEN == FAKE_BLOB_DEFAULT_TOKEN


@tid("EXTRACT-01")
@fr("FR-10")
async def test_EXTRACT_01_fake_blob_client_token_upload_rules(store: FakeBlobStorage, blob_url: str) -> None:
    ticket = await store.upload_ticket("receipts/x/a.jpg", "image/jpeg", len(JPEG))
    assert ticket.method == "PUT" and ticket.blob and ticket.blob["pathname"] == "receipts/x/a.jpg"
    assert ticket.upload_url is not None
    ok = httpx.put(ticket.upload_url, content=JPEG, headers=ticket.headers)
    assert ok.status_code == 200, ok.text
    assert ok.json()["pathname"] == "receipts/x/a.jpg" and ok.json()["contentType"] == "image/jpeg"
    # the same token can't overwrite, can't target another pathname, can't exceed the size or type
    assert httpx.put(ticket.upload_url, content=JPEG, headers=ticket.headers).status_code == 400
    assert _put(blob_url, "receipts/x/other.jpg", _token("receipts/x/b.jpg"), JPEG).status_code == 403
    assert _put(blob_url, "receipts/x/c.jpg", _token("receipts/x/c.jpg"), b"\xff" * 101).status_code == 400
    assert _put(blob_url, "receipts/x/d.jpg", _token("receipts/x/d.jpg"), JPEG, "image/gif").status_code == 400
    wildcard = _token("receipts/x/e.png", allowedContentTypes=["image/*"])
    assert _put(blob_url, "receipts/x/e.png", wildcard, JPEG, "image/png").status_code == 200
    expired = _token("receipts/x/f.jpg", validUntil=int(time.time() * 1000) - 1)
    assert _put(blob_url, "receipts/x/f.jpg", expired, JPEG).status_code == 403
    forged = mint_client_token("vercel_blob_rw_fakestore_wrongsecret", {"pathname": "receipts/x/g.jpg"})
    assert _put(blob_url, "receipts/x/g.jpg", forged, JPEG).status_code == 403
    assert (await store.head("receipts/x/f.jpg")) is None


@tid("EXTRACT-10")
@fr("FR-18")
async def test_EXTRACT_10_fake_blob_is_private_with_short_lived_urls(store: FakeBlobStorage, blob_url: str) -> None:
    await store.put("receipts/y/a.pdf", b"%PDF-1.7 hello", "application/pdf")
    info = await store.head("receipts/y/a.pdf")
    assert info is not None and info.size == 14 and info.content_type == "application/pdf"
    assert await store.get("receipts/y/a.pdf") == b"%PDF-1.7 hello"
    assert await store.get("receipts/y/a.pdf", first_bytes=4) == b"%PDF"
    # no anonymous reads of a private blob
    assert httpx.get(f"{blob_url}/blob/receipts/y/a.pdf").status_code == 403
    url = await store.view_url("receipts/y/a.pdf")
    got = httpx.get(url)
    assert got.status_code == 200 and got.content == b"%PDF-1.7 hello"
    assert got.headers["content-disposition"].startswith("attachment")
    assert httpx.get(url.replace("sig=", "sig=0")).status_code == 403
    past = int(time.time()) - 1
    sig = sign_read(FAKE_BLOB_DEFAULT_TOKEN, "receipts/y/a.pdf", past)
    stale = f"{blob_url}/blob/receipts/y/a.pdf?expires={past}&sig={sig}"
    assert httpx.get(stale).status_code == 403
    await store.delete("receipts/y/a.pdf")
    await store.delete("receipts/y/a.pdf")  # deleting twice is fine
    assert await store.head("receipts/y/a.pdf") is None
    listed = httpx.get(f"{blob_url}/", headers={"authorization": f"Bearer {FAKE_BLOB_DEFAULT_TOKEN}"}).json()
    assert listed["blobs"] == []
