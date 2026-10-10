"""Receipt file storage behind one interface (ADR-0015, BUILD_ALL C2).

Files go from the browser straight to storage, never through a function body (4.5 MB limit). The
API decides who may upload (`app.policy`) and hands out an upload ticket; it later reads the stored
object back (size check, magic bytes, text-PDF parsing), mints short-lived read URLs for house
members and deletes files at retention time.

    S3Storage          MinIO locally (`STORAGE_PROVIDER=s3`). SigV4 presigned PUT/GET and signed
                       server-side calls, implemented with hmac/hashlib + httpx (no boto3).
    FakeBlobStorage    the in-repo fake Vercel Blob service (`fakeservices/fake_blob.py`;
                       `STORAGE_PROVIDER=fakeblob`, PROFILE=vercel locally and in tests).
    VercelBlobStorage  Vercel Blob in production (`STORAGE_PROVIDER=vercel_blob`): Vercel's Python SDK
                       (`vercel.blob`) with BLOB_READ_WRITE_TOKEN for head/get/put/delete. The browser's
                       client token is minted by the Next.js `/api/blob-upload` route and signed view
                       URLs by `/api/blob-view` (only `@vercel/blob` has them; ADR-0015, ADR-0024).

Signatures use the real wall clock (`SystemClock`), not the test-controllable clock: MinIO and Blob
check expiry against real time.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

import httpx

from app.clock import Clock, SystemClock
from app.settings import Settings

UPLOAD_TTL = timedelta(minutes=10)
VIEW_TTL = timedelta(minutes=5)
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=5.0)
# The fake blob service's default read-write token (also in fakeservices/fake_blob.py, which can't
# import app code). Used with STORAGE_PROVIDER=fakeblob when BLOB_READ_WRITE_TOKEN is empty.
FAKE_BLOB_DEFAULT_TOKEN = "vercel_blob_rw_fakestore_fakeblob0secret0for0local0dev0only"  # noqa: S105 - local fake only
VERCEL_BLOB_API_URL = "https://blob.vercel-storage.com"
BLOB_API_VERSION = "7"
BLOB_UPLOAD_ROUTE = "/api/blob-upload"  # Next.js route; vercel.json sends it to web, not the API


class StorageError(Exception):
    """Storage answered with something unexpected (network error, 5xx, refused signature)."""


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str | None = None


@dataclass(frozen=True)
class UploadTicket:
    """What the browser needs to put one file into storage.

    `method` is "PUT" (send the file to `upload_url` with `headers`) or "blob-client" (Vercel Blob:
    `@vercel/blob/client` uploads to `blob["pathname"]` with a client token from the Next.js route).
    """

    provider: str
    storage_key: str
    method: str
    upload_url: str | None
    headers: dict[str, str]
    content_type: str
    max_bytes: int
    expires_at: datetime
    blob: dict[str, Any] | None = None

    def wire(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "storage_key": self.storage_key,
            "method": self.method,
            "upload_url": self.upload_url,
            "headers": self.headers,
            "content_type": self.content_type,
            "max_bytes": self.max_bytes,
            "expires_at": self.expires_at.isoformat(),
            "blob": self.blob,
        }


class Storage(Protocol):
    provider: str

    async def upload_ticket(self, key: str, mime: str, max_bytes: int) -> UploadTicket: ...

    async def head(self, key: str) -> ObjectInfo | None: ...

    async def get(self, key: str, *, first_bytes: int | None = None) -> bytes: ...

    async def view_url(self, key: str, ttl: timedelta = VIEW_TTL) -> str: ...

    async def delete(self, key: str) -> None: ...

    async def put(self, key: str, data: bytes, mime: str) -> None: ...


def new_object_token() -> str:
    """The unguessable part of a storage key (192 random bits)."""
    return secrets.token_urlsafe(24)


# ---- S3 / MinIO (SigV4) ---------------------------------------------------------------------------

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _uri_encode(value: str, *, keep_slash: bool = False) -> str:
    """RFC 3986 encoding as SigV4 wants it: only A-Z a-z 0-9 - _ . ~ stay unescaped."""
    return quote(value, safe="-_.~/" if keep_slash else "-_.~")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


@dataclass(frozen=True)
class SigV4:
    access_key: str
    secret_key: str
    region: str
    service: str = "s3"

    def signing_key(self, day: str) -> bytes:
        k = _hmac(f"AWS4{self.secret_key}".encode(), day)
        k = _hmac(k, self.region)
        k = _hmac(k, self.service)
        return _hmac(k, "aws4_request")

    def scope(self, day: str) -> str:
        return f"{day}/{self.region}/{self.service}/aws4_request"

    def signature(
        self,
        *,
        method: str,
        path: str,
        query: dict[str, str],
        headers: dict[str, str],
        payload_hash: str,
        when: datetime,
    ) -> tuple[str, str]:
        """(signed header names, hex signature) for one request."""
        amz_date = when.strftime("%Y%m%dT%H%M%SZ")
        day = amz_date[:8]
        canonical_query = "&".join(
            f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items(), key=lambda kv: kv[0])
        )
        lowered = {k.lower(): " ".join(str(v).split()) for k, v in headers.items()}
        names = sorted(lowered)
        canonical_headers = "".join(f"{n}:{lowered[n]}\n" for n in names)
        signed = ";".join(names)
        canonical = "\n".join(
            [method, _uri_encode(path, keep_slash=True), canonical_query, canonical_headers, signed, payload_hash]
        )
        to_sign = "\n".join(
            ["AWS4-HMAC-SHA256", amz_date, self.scope(day), hashlib.sha256(canonical.encode()).hexdigest()]
        )
        return signed, hmac.new(self.signing_key(day), to_sign.encode(), hashlib.sha256).hexdigest()


class S3Storage:
    """Path-style S3 (MinIO): `{endpoint}/{bucket}/{key}`."""

    provider = "s3"

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        *,
        clock: Clock | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.bucket = bucket
        self.signer = SigV4(access_key, secret_key, region)
        self.clock = clock or SystemClock()
        self._transport = transport
        self._host = urlsplit(self.endpoint).netloc

    def _path(self, key: str) -> str:
        return f"/{self.bucket}/{key}"

    def _url(self, path: str, query: dict[str, str] | None = None) -> str:
        url = self.endpoint + _uri_encode(path, keep_slash=True)
        if query:
            url += "?" + "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items()))
        return url

    def presign(
        self,
        method: str,
        key: str,
        ttl: timedelta,
        *,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ) -> str:
        """Query-string SigV4 (a URL the browser can use as is). Extra `headers` are signed and the
        client must send them exactly (Content-Type, Content-Length)."""
        when = self.clock.now().astimezone(UTC)
        amz_date = when.strftime("%Y%m%dT%H%M%SZ")
        signed_headers = {"host": self._host, **{k.lower(): v for k, v in (headers or {}).items()}}
        q = {
            **(query or {}),
            "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
            "X-Amz-Credential": f"{self.signer.access_key}/{self.signer.scope(amz_date[:8])}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(int(ttl.total_seconds())),
            "X-Amz-SignedHeaders": ";".join(sorted(signed_headers)),
        }
        path = self._path(key)
        _, sig = self.signer.signature(
            method=method, path=path, query=q, headers=signed_headers, payload_hash="UNSIGNED-PAYLOAD", when=when
        )
        return self._url(path, {**q, "X-Amz-Signature": sig})

    async def _request(
        self,
        method: str,
        key: str,
        *,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
        unsigned_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """A server-side call signed with the Authorization header."""
        when = self.clock.now().astimezone(UTC)
        payload_hash = hashlib.sha256(content).hexdigest() if content else EMPTY_SHA256
        signed_headers = {
            "host": self._host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": when.strftime("%Y%m%dT%H%M%SZ"),
            **{k.lower(): v for k, v in (headers or {}).items()},
        }
        path = self._path(key)
        names, sig = self.signer.signature(
            method=method, path=path, query={}, headers=signed_headers, payload_hash=payload_hash, when=when
        )
        auth = (
            f"AWS4-HMAC-SHA256 Credential={self.signer.access_key}/{self.signer.scope(when.strftime('%Y%m%d'))}, "
            f"SignedHeaders={names}, Signature={sig}"
        )
        send = {k: v for k, v in signed_headers.items() if k != "host"}
        send.update(unsigned_headers or {})
        send["Authorization"] = auth
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=HTTP_TIMEOUT) as client:
                return await client.request(method, self._url(path), content=content or None, headers=send)
        except httpx.HTTPError as exc:
            raise StorageError(f"s3 {method} failed: {type(exc).__name__}") from exc

    async def upload_ticket(self, key: str, mime: str, max_bytes: int) -> UploadTicket:
        # Signing Content-Length pins the upload to exactly the size that was authorized.
        headers = {"Content-Type": mime, "Content-Length": str(max_bytes)}
        url = self.presign("PUT", key, UPLOAD_TTL, headers=headers)
        return UploadTicket(
            provider=self.provider,
            storage_key=key,
            method="PUT",
            upload_url=url,
            headers={"Content-Type": mime},
            content_type=mime,
            max_bytes=max_bytes,
            expires_at=self.clock.now() + UPLOAD_TTL,
        )

    async def head(self, key: str) -> ObjectInfo | None:
        resp = await self._request("HEAD", key)
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise StorageError(f"s3 HEAD answered {resp.status_code}")
        return ObjectInfo(int(resp.headers.get("content-length", "0")), resp.headers.get("content-type"))

    async def get(self, key: str, *, first_bytes: int | None = None) -> bytes:
        extra = {"Range": f"bytes=0-{first_bytes - 1}"} if first_bytes else None
        resp = await self._request("GET", key, unsigned_headers=extra)
        if resp.status_code not in (200, 206):
            raise StorageError(f"s3 GET answered {resp.status_code}")
        return resp.content if not first_bytes else resp.content[:first_bytes]

    async def view_url(self, key: str, ttl: timedelta = VIEW_TTL) -> str:
        # Served from storage's own origin as an attachment (PRD §13 Security, SEC-04).
        return self.presign("GET", key, ttl, query={"response-content-disposition": "attachment"})

    async def delete(self, key: str) -> None:
        resp = await self._request("DELETE", key)
        if resp.status_code not in (200, 204, 404):
            raise StorageError(f"s3 DELETE answered {resp.status_code}")

    async def put(self, key: str, data: bytes, mime: str) -> None:
        resp = await self._request("PUT", key, content=data, headers={"content-type": mime})
        if resp.status_code not in (200, 201):
            raise StorageError(f"s3 PUT answered {resp.status_code}")


# ---- Vercel Blob (and the local fake) ---------------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def mint_client_token(read_write_token: str, payload: dict[str, Any]) -> str:
    """A Vercel Blob client token, as `@vercel/blob`'s `generateClientTokenFromReadWriteToken` makes it:
    `vercel_blob_client_<storeId>_` + base64("<hex HMAC-SHA256(rw token, p)>.<p>") where p is the
    base64 JSON payload (pathname, allowedContentTypes, maximumSizeInBytes, validUntil in ms, ...).
    Only the fake path uses it server-side; production mints in the Next.js route (ADR-0015)."""
    store_id = read_write_token.split("_")[3] if read_write_token.count("_") >= 4 else ""
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signature = hmac.new(read_write_token.encode(), body.encode(), hashlib.sha256).hexdigest()
    return f"vercel_blob_client_{store_id}_{_b64(f'{signature}.{body}'.encode())}"


def sign_read(read_write_token: str, pathname: str, expires: int) -> str:
    """Signature of a short-lived private read URL (`?expires=<unix s>&sig=<hex>`)."""
    msg = f"GET\n{pathname}\n{expires}".encode()
    return hmac.new(read_write_token.encode(), msg, hashlib.sha256).hexdigest()


@dataclass
class BlobApiStorage:
    """Server-side calls to the Vercel Blob REST API (`PUT /?pathname=`, `GET /?url=`, `POST /delete`)
    with the read-write token. Reads of private blobs send the token as a bearer; views get a
    short-lived signed URL."""

    api_url: str
    token: str
    provider: str = "vercel_blob"
    mint_tokens: bool = False  # the fake: the API mints the client token itself
    clock: Clock = field(default_factory=SystemClock)
    transport: httpx.AsyncBaseTransport | None = None

    def _headers(self, **extra: str) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}", "x-api-version": BLOB_API_VERSION, **extra}

    async def _call(self, method: str, url: str, **kw: Any) -> httpx.Response:
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=HTTP_TIMEOUT) as client:
                return await client.request(method, url, **kw)
        except httpx.HTTPError as exc:
            raise StorageError(f"blob {method} failed: {type(exc).__name__}") from exc

    async def upload_ticket(self, key: str, mime: str, max_bytes: int) -> UploadTicket:
        expires = self.clock.now() + UPLOAD_TTL
        valid_until_ms = int(expires.timestamp()) * 1000
        blob: dict[str, Any] = {
            "pathname": key,
            "allowed_content_types": [mime],
            "maximum_size_in_bytes": max_bytes,
            "valid_until": valid_until_ms,
            "add_random_suffix": False,
            "client_token": None,
            # `@vercel/blob/client` upload(): the web service's route mints the token (ADR-0015)
            "handle_upload_url": None if self.mint_tokens else BLOB_UPLOAD_ROUTE,
        }
        upload_url: str | None = None
        headers: dict[str, str] = {}
        method = "blob-client"
        if self.mint_tokens:
            token = mint_client_token(
                self.token,
                {
                    "pathname": key,
                    "allowedContentTypes": [mime],
                    "maximumSizeInBytes": max_bytes,
                    "validUntil": valid_until_ms,
                    "addRandomSuffix": False,
                },
            )
            blob["client_token"] = token
            method = "PUT"
            upload_url = f"{self.api_url.rstrip('/')}/?pathname={quote(key, safe='/')}"
            headers = {
                "authorization": f"Bearer {token}",
                "x-api-version": BLOB_API_VERSION,
                "x-content-type": mime,
                "x-add-random-suffix": "0",
            }
        return UploadTicket(
            provider=self.provider,
            storage_key=key,
            method=method,
            upload_url=upload_url,
            headers=headers,
            content_type=mime,
            max_bytes=max_bytes,
            expires_at=expires,
            blob=blob,
        )

    async def _meta(self, key: str) -> dict[str, Any] | None:
        resp = await self._call("GET", f"{self.api_url.rstrip('/')}/", params={"url": key}, headers=self._headers())
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise StorageError(f"blob head answered {resp.status_code}")
        meta: dict[str, Any] = resp.json()
        return meta

    async def head(self, key: str) -> ObjectInfo | None:
        meta = await self._meta(key)
        if meta is None:
            return None
        return ObjectInfo(int(meta["size"]), meta.get("contentType"))

    async def get(self, key: str, *, first_bytes: int | None = None) -> bytes:
        meta = await self._meta(key)
        if meta is None:
            raise StorageError("blob not found")
        extra = {"range": f"bytes=0-{first_bytes - 1}"} if first_bytes else {}
        resp = await self._call("GET", str(meta["url"]), headers=self._headers(**extra))
        if resp.status_code not in (200, 206):
            raise StorageError(f"blob read answered {resp.status_code}")
        return resp.content if not first_bytes else resp.content[:first_bytes]

    async def view_url(self, key: str, ttl: timedelta = VIEW_TTL) -> str:
        meta = await self._meta(key)
        if meta is None:
            raise StorageError("blob not found")
        expires = int((self.clock.now() + ttl).timestamp())
        url = str(meta["url"])
        return f"{url}?expires={expires}&sig={sign_read(self.token, key, expires)}"

    async def delete(self, key: str) -> None:
        resp = await self._call(
            "POST", f"{self.api_url.rstrip('/')}/delete", json={"urls": [key]}, headers=self._headers()
        )
        if resp.status_code not in (200, 204):
            raise StorageError(f"blob delete answered {resp.status_code}")

    async def put(self, key: str, data: bytes, mime: str) -> None:
        resp = await self._call(
            "PUT",
            f"{self.api_url.rstrip('/')}/",
            params={"pathname": key},
            content=data,
            headers=self._headers(**{"x-content-type": mime, "x-add-random-suffix": "0", "x-allow-overwrite": "1"}),
        )
        if resp.status_code != 200:
            raise StorageError(f"blob put answered {resp.status_code}")


class FakeBlobStorage(BlobApiStorage):
    """The in-repo fake Vercel Blob service; the API mints the client token itself (no Next.js route)."""

    def __init__(self, url: str, token: str = FAKE_BLOB_DEFAULT_TOKEN, **kw: Any) -> None:
        super().__init__(api_url=url, token=token, provider="fakeblob", mint_tokens=True, **kw)


BLOB_VIEW_ROUTE = "/api/blob-view"  # Next.js route: verifies `sig`, mints a Vercel signed URL, redirects


@dataclass
class VercelBlobStorage:
    """Production storage through Vercel's official Python SDK (`vercel.blob`, private store).

    The SDK has no signed URLs (only `@vercel/blob` does), so a view is a same-origin link to the
    web service's `/api/blob-view` route, signed here with HMAC over the read-write token (`sign_read`)
    and valid for minutes; that route checks the signature and redirects to a Vercel signed URL.
    Uploads go browser → Blob with a client token minted by `/api/blob-upload` (ADR-0015)."""

    token: str
    provider: str = "vercel_blob"
    clock: Clock = field(default_factory=SystemClock)

    def _client(self) -> Any:
        from vercel.blob import AsyncBlobClient

        return AsyncBlobClient(token=self.token)

    async def upload_ticket(self, key: str, mime: str, max_bytes: int) -> UploadTicket:
        expires = self.clock.now() + UPLOAD_TTL
        return UploadTicket(
            provider=self.provider,
            storage_key=key,
            method="blob-client",
            upload_url=None,
            headers={},
            content_type=mime,
            max_bytes=max_bytes,
            expires_at=expires,
            blob={
                "pathname": key,
                "allowed_content_types": [mime],
                "maximum_size_in_bytes": max_bytes,
                "valid_until": int(expires.timestamp()) * 1000,
                "add_random_suffix": False,
                "client_token": None,
                "handle_upload_url": BLOB_UPLOAD_ROUTE,
            },
        )

    async def head(self, key: str) -> ObjectInfo | None:
        from vercel.blob import BlobNotFoundError

        try:
            meta = await self._client().head(key)
        except BlobNotFoundError:
            return None
        except Exception as exc:
            raise StorageError(f"blob head failed: {type(exc).__name__}") from exc
        return ObjectInfo(int(meta.size), meta.content_type)

    async def get(self, key: str, *, first_bytes: int | None = None) -> bytes:
        try:
            result = await self._client().get(key, access="private", use_cache=False)
        except Exception as exc:
            raise StorageError(f"blob get failed: {type(exc).__name__}") from exc
        if result is None:
            raise StorageError("blob not found")
        content = bytes(result.content)
        return content[:first_bytes] if first_bytes else content

    async def view_url(self, key: str, ttl: timedelta = VIEW_TTL) -> str:
        expires = int((self.clock.now() + ttl).timestamp())
        return (
            f"{BLOB_VIEW_ROUTE}?pathname={quote(key, safe='')}&expires={expires}"
            f"&sig={sign_read(self.token, key, expires)}"
        )

    async def delete(self, key: str) -> None:
        try:
            await self._client().delete(key)
        except Exception as exc:
            raise StorageError(f"blob delete failed: {type(exc).__name__}") from exc

    async def put(self, key: str, data: bytes, mime: str) -> None:
        try:
            await self._client().put(key, data, access="private", content_type=mime, overwrite=True)
        except Exception as exc:
            raise StorageError(f"blob put failed: {type(exc).__name__}") from exc


def storage_from_settings(settings: Settings) -> Storage:
    if settings.storage_provider == "s3":
        if not (settings.s3_endpoint and settings.s3_access_key and settings.s3_secret_key):
            raise StorageError("S3_ENDPOINT, S3_ACCESS_KEY and S3_SECRET_KEY are required with STORAGE_PROVIDER=s3")
        return S3Storage(
            settings.s3_endpoint,
            settings.s3_bucket,
            settings.s3_access_key.get_secret_value(),
            settings.s3_secret_key.get_secret_value(),
            settings.s3_region,
        )
    token = settings.blob_read_write_token.get_secret_value() if settings.blob_read_write_token else None
    if settings.storage_provider == "fakeblob":
        return FakeBlobStorage(settings.fake_blob_url, token or FAKE_BLOB_DEFAULT_TOKEN)
    if not token:
        raise StorageError("BLOB_READ_WRITE_TOKEN is required with STORAGE_PROVIDER=vercel_blob")
    return VercelBlobStorage(token)
