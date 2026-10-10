"""Web Push sender (PRD §20.2): RFC 8291 encryption + RFC 8292 VAPID via pywebpush.

With PUSH_PROVIDER=fake the same code path runs; subscriptions simply point at the fake push
service, which verifies and decrypts what it receives. Delivery pipeline lands in Milestone 9.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal

from pywebpush import WebPushException, webpush

Urgency = Literal["very-low", "low", "normal", "high"]
MAX_PAYLOAD_BYTES = 2048  # PRD §20.2


@dataclass(frozen=True)
class SubscriptionInfo:
    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True)
class PushResult:
    status: int

    @property
    def gone(self) -> bool:  # delete the subscription (PUSH-05)
        return self.status in (404, 410)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class WebPushSender:
    def __init__(self, vapid_private_key: str, vapid_subject: str) -> None:
        self._key = vapid_private_key
        self._subject = vapid_subject

    def _send_sync(self, sub: SubscriptionInfo, body: bytes, ttl: int, urgency: Urgency, topic: str | None) -> int:
        headers: dict[str, str] = {"Urgency": urgency}
        if topic:
            headers["Topic"] = topic
        try:
            resp = webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=body,
                vapid_private_key=self._key,
                vapid_claims={"sub": self._subject},
                ttl=ttl,
                headers=headers,
                content_encoding="aes128gcm",
                timeout=10,
            )
        except WebPushException as exc:
            if exc.response is not None:
                return int(exc.response.status_code)
            raise
        return int(resp.status_code)

    async def send(
        self,
        sub: SubscriptionInfo,
        payload: dict[str, Any],
        *,
        ttl: int,
        urgency: Urgency,
        topic: str | None = None,
    ) -> PushResult:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if len(body) > MAX_PAYLOAD_BYTES:
            raise ValueError(f"push payload is {len(body)} bytes; the limit is {MAX_PAYLOAD_BYTES}")
        status = await asyncio.to_thread(self._send_sync, sub, body, ttl, urgency, topic)
        return PushResult(status)
