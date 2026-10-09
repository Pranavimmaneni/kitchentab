"""Self-test of the fake push service (TEST_PLAN §2): the app's real sender, real encryption."""

from __future__ import annotations

import base64
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02

from app.adapters.push import MAX_PAYLOAD_BYTES, SubscriptionInfo, WebPushSender
from fakeservices.fake_push import create_app, verify_vapid
from tests.harness.servers import free_port, serve


@pytest.fixture(scope="module")
def fake_push() -> Iterator[str]:
    port = free_port()
    with serve(create_app(f"http://127.0.0.1:{port}"), port) as url:
        yield url


@pytest.fixture(scope="module")
def vapid_private_key() -> str:
    v = Vapid02()
    v.generate_keys()
    raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _subscribe(base: str, user: str) -> tuple[str, SubscriptionInfo]:
    data = httpx.post(f"{base}/subscriptions", json={"user": user}).json()
    return data["id"], SubscriptionInfo(data["endpoint"], data["keys"]["p256dh"], data["keys"]["auth"])


def _received(base: str, user: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = httpx.get(f"{base}/__test/push", params={"user": user}).json()
    return result


async def test_decrypts_a_real_web_push(fake_push: str, vapid_private_key: str) -> None:
    _, sub = _subscribe(fake_push, "pranav")
    sender = WebPushSender(vapid_private_key, "mailto:hello@example.com")
    payload = {"title": "Kabir asked for your chicken thighs", "body": "A bit.", "url": "/kitchen/requests?id=r1",
               "tag": "ask-r1", "badge": 2, "event_id": "1:42"}
    result = await sender.send(sub, payload, ttl=172800, urgency="high", topic="ask-r1")
    assert result.ok and result.status == 201
    [got] = _received(fake_push, "pranav")
    assert got["payload"] == payload
    assert (got["ttl"], got["urgency"], got["topic"]) == (172800, "high", "ask-r1")
    assert got["vapid_sub"] == "mailto:hello@example.com"


async def test_forced_status_codes(fake_push: str, vapid_private_key: str) -> None:
    sub_id, sub = _subscribe(fake_push, "arjun")
    sender = WebPushSender(vapid_private_key, "mailto:hello@example.com")
    for status in (410, 404, 429, 500):
        httpx.post(f"{fake_push}/__control/respond", json={"status": status, "sub_id": sub_id, "times": 1})
        result = await sender.send(sub, {"title": "t"}, ttl=60, urgency="normal")
        assert result.status == status
        assert result.gone == (status in (404, 410))
    assert (await sender.send(sub, {"title": "t"}, ttl=60, urgency="normal")).status == 201


async def test_unknown_subscription_is_gone(fake_push: str, vapid_private_key: str) -> None:
    sender = WebPushSender(vapid_private_key, "mailto:hello@example.com")
    _, sub = _subscribe(fake_push, "meera")
    stale = SubscriptionInfo(sub.endpoint.rsplit("/", 1)[0] + "/nope", sub.p256dh, sub.auth)
    assert (await sender.send(stale, {"title": "t"}, ttl=60, urgency="normal")).gone


async def test_sender_refuses_payloads_over_two_kilobytes(fake_push: str, vapid_private_key: str) -> None:
    _, sub = _subscribe(fake_push, "kabir")
    sender = WebPushSender(vapid_private_key, "mailto:hello@example.com")
    with pytest.raises(ValueError, match="limit"):
        await sender.send(sub, {"body": "x" * MAX_PAYLOAD_BYTES}, ttl=60, urgency="normal")


def test_vapid_with_wrong_audience_is_rejected(vapid_private_key: str) -> None:
    v = Vapid02.from_raw(vapid_private_key.encode())
    headers = v.sign({"aud": "https://elsewhere.example", "sub": "mailto:a@b.c", "exp": int(time.time()) + 600})
    with pytest.raises(Exception, match="aud"):
        verify_vapid(headers["Authorization"], "http://127.0.0.1:1", time.time())


def test_vapid_signed_by_another_key_is_rejected(vapid_private_key: str) -> None:
    v = Vapid02.from_raw(vapid_private_key.encode())
    other = Vapid02()
    other.generate_keys()
    claims = {"aud": "http://127.0.0.1:1", "sub": "mailto:a@b.c", "exp": int(time.time()) + 600}
    token = v.sign(claims)["Authorization"].split("t=", 1)[1].split(",", 1)[0]
    other_k = base64.urlsafe_b64encode(
        other.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ).rstrip(b"=").decode()
    with pytest.raises(Exception, match="signature"):
        verify_vapid(f"vapid t={token}, k={other_k}", "http://127.0.0.1:1", time.time())
