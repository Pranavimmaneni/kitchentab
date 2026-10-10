"""Fake Vercel Blob service (TEST_PLAN §2, ADR-0015, ADR-0017). Local dev, PROFILE=vercel and tests only.

Emulates the parts of the Vercel Blob REST API that KitchenTab uses, closely enough for the real
client code paths (the API's `BlobApiStorage`, `@vercel/blob` pointed here with
`VERCEL_BLOB_API_URL`) to run unchanged:

* `PUT /?pathname=<p>` (and the older `PUT /<p>`) uploads a body. Authorization is a bearer
  read-write token (`vercel_blob_rw_<store>_<secret>`) or a client token minted from it
  (`vercel_blob_client_<store>_` + base64("<hex HMAC-SHA256(rw, payload)>.<base64 JSON payload>")).
  A client token pins the pathname and enforces `validUntil` (ms), `allowedContentTypes`
  (`image/*` wildcards allowed) and `maximumSizeInBytes`. Headers: `x-content-type`,
  `x-add-random-suffix`, `x-allow-overwrite`. Answers `{url, downloadUrl, pathname, contentType,
  contentDisposition}`.
* `GET /?url=<url or pathname>` is head (metadata with `size`), 404 when missing; `GET /` lists.
* `POST /delete` `{"urls": [...]}` deletes (missing ones are ignored).
* Blobs are **private**: `GET /blob/<p>` needs the read-write bearer token or a short-lived signed
  URL `?expires=<unix s>&sig=<hex HMAC-SHA256(rw, "GET\\n<p>\\n<expires>")>`; signed reads are
  served with `Content-Disposition: attachment`. `Range: bytes=a-b` is honoured.
* `GET /__test/blobs`, `DELETE /__test/blobs` inspect and clear the store.

Errors use the API's shape: `{"error": {"code": ..., "message": ...}}`.
"""

from __future__ import annotations

import base64
import binascii
import fnmatch
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

DEFAULT_TOKEN = "vercel_blob_rw_fakestore_fakeblob0secret0for0local0dev0only"
PATHNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-/]{0,950}$")


@dataclass
class Blob:
    pathname: str
    data: bytes
    content_type: str
    uploaded_at: float


@dataclass
class State:
    base_url: str
    token: str
    blobs: dict[str, Blob] = field(default_factory=dict)


class BlobError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def sign_read(token: str, pathname: str, expires: int) -> str:
    return hmac.new(token.encode(), f"GET\n{pathname}\n{expires}".encode(), hashlib.sha256).hexdigest()


def verify_client_token(rw_token: str, client_token: str, now_ms: int) -> dict[str, Any]:
    """Decode and check a client token minted from `rw_token`; returns its payload."""
    prefix = "vercel_blob_client_"
    if not client_token.startswith(prefix):
        raise BlobError(403, "forbidden", "Invalid token")
    _store, _, encoded = client_token[len(prefix) :].partition("_")
    try:
        signature, _, payload_b64 = base64.b64decode(encoded, validate=True).decode().partition(".")
        payload: dict[str, Any] = json.loads(base64.b64decode(payload_b64))
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise BlobError(403, "forbidden", "Invalid token") from exc
    expected = hmac.new(rw_token.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise BlobError(403, "forbidden", "Invalid token")
    if int(payload.get("validUntil", 0)) <= now_ms:
        raise BlobError(403, "forbidden", "Token expired")
    return payload


def _content_type_allowed(content_type: str, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    base = content_type.split(";")[0].strip().lower()
    return any(fnmatch.fnmatchcase(base, pattern.lower()) for pattern in allowed)


def create_app(base_url: str | None = None, token: str | None = None) -> FastAPI:
    state = State(
        base_url=(base_url or os.environ.get("FAKE_BLOB_BASE_URL", "http://localhost:4012")).rstrip("/"),
        token=token or os.environ.get("FAKE_BLOB_RW_TOKEN") or DEFAULT_TOKEN,
    )
    app = FastAPI(title="KitchenTab fake Vercel Blob service")
    app.state.fake = state
    # Browsers upload straight here from the web origin (client uploads).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "PUT", "POST", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["content-length", "content-range"],
    )

    @app.exception_handler(BlobError)
    async def _blob_error(request: Request, exc: BlobError) -> JSONResponse:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    def url_for(pathname: str) -> str:
        return f"{state.base_url}/blob/{quote(pathname, safe='/')}"

    def meta(blob: Blob) -> dict[str, Any]:
        url = url_for(blob.pathname)
        return {
            "url": url,
            "downloadUrl": f"{url}?download=1",
            "pathname": blob.pathname,
            "size": len(blob.data),
            "uploadedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(blob.uploaded_at)),
            "contentType": blob.content_type,
            "contentDisposition": f'attachment; filename="{blob.pathname.rsplit("/", 1)[-1]}"',
            "cacheControl": "private, max-age=0",
        }

    def bearer(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise BlobError(403, "forbidden", "Missing token")
        return auth[7:].strip()

    def require_rw(request: Request) -> None:
        if not hmac.compare_digest(bearer(request), state.token):
            raise BlobError(403, "forbidden", "Invalid token")

    def resolve(url_or_pathname: str) -> str:
        if url_or_pathname.startswith(("http://", "https://")):
            path = unquote(urlsplit(url_or_pathname).path)
            return path.removeprefix("/blob/").lstrip("/")
        return url_or_pathname.lstrip("/")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def _upload(pathname: str, request: Request) -> dict[str, Any]:
        if not PATHNAME_RE.match(pathname) or ".." in pathname:
            raise BlobError(400, "bad_request", "Invalid pathname")
        token = bearer(request)
        content_type = request.headers.get("x-content-type") or request.headers.get("content-type") or ""
        content_type = content_type or "application/octet-stream"
        allow_overwrite = request.headers.get("x-allow-overwrite") == "1"
        random_suffix = request.headers.get("x-add-random-suffix") == "1"
        limit: int | None = None
        if not hmac.compare_digest(token, state.token):
            payload = verify_client_token(state.token, token, int(time.time() * 1000))
            if payload.get("pathname") != pathname:
                raise BlobError(403, "forbidden", "Token is for a different pathname")
            if not _content_type_allowed(content_type, payload.get("allowedContentTypes")):
                raise BlobError(400, "bad_request", f"Content type {content_type} is not allowed")
            limit = payload.get("maximumSizeInBytes")
            allow_overwrite = bool(payload.get("allowOverwrite", False))
            random_suffix = bool(payload.get("addRandomSuffix", False))
        data = await request.body()
        if limit is not None and len(data) > int(limit):
            raise BlobError(400, "bad_request", f"File is larger than {limit} bytes")
        if random_suffix:
            stem, dot, ext = pathname.rpartition(".")
            suffix = secrets.token_urlsafe(18).replace("_", "").replace("-", "")[:21]
            pathname = f"{stem}-{suffix}.{ext}" if dot else f"{pathname}-{suffix}"
        if pathname in state.blobs and not allow_overwrite:
            raise BlobError(400, "bad_request", "This blob already exists")
        blob = Blob(pathname, data, content_type, time.time())
        state.blobs[pathname] = blob
        m = meta(blob)
        return {k: m[k] for k in ("url", "downloadUrl", "pathname", "contentType", "contentDisposition")}

    @app.put("/")
    async def put_query(request: Request, pathname: str) -> dict[str, Any]:
        return await _upload(pathname, request)

    @app.get("/")
    async def head_or_list(
        request: Request, url: str | None = None, prefix: str = "", limit: int = 1000
    ) -> dict[str, Any]:
        require_rw(request)
        if url is not None:
            blob = state.blobs.get(resolve(url))
            if blob is None:
                raise BlobError(404, "not_found", "The requested blob does not exist")
            return meta(blob)
        found = sorted(p for p in state.blobs if p.startswith(prefix))[: max(1, min(limit, 1000))]
        return {
            "blobs": [{k: v for k, v in meta(state.blobs[p]).items() if k != "contentDisposition"} for p in found],
            "cursor": None,
            "hasMore": False,
        }

    @app.post("/delete")
    async def delete(request: Request) -> Response:
        require_rw(request)
        body = await request.json()
        for item in body.get("urls", []):
            state.blobs.pop(resolve(str(item)), None)
        return Response("null", media_type="application/json")

    @app.get("/blob/{pathname:path}")
    async def read(request: Request, pathname: str, expires: int | None = None, sig: str | None = None) -> Response:
        blob = state.blobs.get(pathname)
        signed = expires is not None and sig is not None
        if signed:
            assert expires is not None and sig is not None
            if expires < int(time.time()):
                raise BlobError(403, "forbidden", "Link expired")
            if not hmac.compare_digest(sig, sign_read(state.token, pathname, expires)):
                raise BlobError(403, "forbidden", "Bad signature")
        else:
            require_rw(request)  # private store: no anonymous reads
        if blob is None:
            raise BlobError(404, "not_found", "The requested blob does not exist")
        headers = {
            "cache-control": "private, no-store",
            "x-content-type-options": "nosniff",
            "accept-ranges": "bytes",
        }
        if signed or request.query_params.get("download") == "1":
            headers["content-disposition"] = meta(blob)["contentDisposition"]
        data = blob.data
        match = re.fullmatch(r"bytes=(\d+)-(\d*)", request.headers.get("range", ""))
        if match:
            start = int(match.group(1))
            end = min(int(match.group(2)) if match.group(2) else len(data) - 1, len(data) - 1)
            headers["content-range"] = f"bytes {start}-{end}/{len(data)}"
            return Response(data[start : end + 1], status_code=206, media_type=blob.content_type, headers=headers)
        return Response(data, media_type=blob.content_type, headers=headers)

    @app.put("/{pathname:path}")
    async def put_path(request: Request, pathname: str) -> dict[str, Any]:
        """Older API versions put the pathname in the URL path."""
        return await _upload(pathname, request)

    @app.get("/__test/blobs")
    async def list_all() -> list[dict[str, Any]]:
        return [meta(b) for b in state.blobs.values()]

    @app.delete("/__test/blobs", status_code=204)
    async def clear() -> Response:
        state.blobs.clear()
        return Response(status_code=204)

    return app
