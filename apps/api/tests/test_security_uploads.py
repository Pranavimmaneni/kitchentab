"""SEC-04: receipt files are served from a separate origin as attachments (PRD §13; TEST_PLAN 3.10).

`GET /api/v1/receipts/{id}/view` hands a house member a short-lived link. The file behind it must
never come from the app's own origin (a hostile "receipt" could otherwise run script as the app)
and must be served with `Content-Disposition: attachment`.

* Blob API stores (the fake Vercel Blob service; PROFILE=vercel locally): the link is on the
  store's origin, and fetching it answers `attachment`; a tampered or expired signature is refused.
* S3 / MinIO (PROFILE=local): the link is a presigned URL on the S3 endpoint that asks S3 to answer
  `response-content-disposition=attachment`.
* Production Vercel Blob: the link is the web service's `/api/blob-view` route (ADR-0024), which
  redirects to `*.blob.vercel-storage.com`; the API side is checked here, the redirect by the web
  route's own test.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from app.adapters.storage import S3Storage
from app.settings import Settings
from tests.harness.api import Api
from tests.harness.security_world import ADMIN, GUEST, MEMBER, OUTSIDER, build_world
from tests.harness.tags import fr, tid

ApiFactory = Callable[[], Api]


def origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@tid("SEC-04")
@fr("FR-18")
async def test_SEC_04_receipt_view_is_another_origin_and_an_attachment(
    api: ApiFactory, app: Any, settings: Settings
) -> None:
    world = await build_world(api, app, receipts=True)
    h, storage = world.home, world.storage
    assert storage is not None and h.receipt_id
    for role in (ADMIN, MEMBER):
        view = await world.who[role].get(f"/api/v1/receipts/{h.receipt_id}/view")
        assert view.status_code == 200, view.text
        assert view.headers["Cache-Control"].startswith("private, no-store")
        url = view.json()["url"]
        assert urlsplit(url).scheme in ("http", "https") and urlsplit(url).netloc, url
        assert origin(url) != origin(settings.web_origin), f"receipt served from the app's own origin: {url}"
        async with httpx.AsyncClient(transport=storage.transport) as blob:
            got = await blob.get(url)
            assert got.status_code == 200, got.text
            assert got.headers["content-disposition"].startswith("attachment"), got.headers
            assert not got.headers.get("content-type", "").startswith("text/html")
            # The signature is the permission: tampered or expired links don't open the file.
            query = parse_qs(urlsplit(url).query)
            base = url.split("?", 1)[0]
            forged = await blob.get(base, params={"expires": query["expires"][0], "sig": "0" * 64})
            assert forged.status_code in (401, 403), forged.status_code
            expired = await blob.get(base, params={"expires": "1", "sig": query["sig"][0]})
            assert expired.status_code in (401, 403), expired.status_code
            assert (await blob.get(base)).status_code in (401, 403), "no signature, no file"
    for role in (GUEST, OUTSIDER):
        assert (await world.who[role].get(f"/api/v1/receipts/{h.receipt_id}/view")).status_code in (403, 404)


@tid("SEC-04")
async def test_SEC_04_s3_links_are_presigned_on_the_storage_origin_as_attachments(settings: Settings) -> None:
    store = S3Storage("http://minio.test:9000", "kitchentab-receipts", "access", "secret-key-0123456789")
    url = await store.view_url("receipts/abc/receipt.jpg", timedelta(minutes=5))
    assert origin(url) == "http://minio.test:9000" != origin(settings.web_origin)
    query = parse_qs(urlsplit(url).query)
    assert query["response-content-disposition"] == ["attachment"]
    assert "X-Amz-Signature" in query and query["X-Amz-Expires"] == ["300"]
    assert "secret-key-0123456789" not in url
