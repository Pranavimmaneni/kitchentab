"""Self-tests of the in-process fakes: SMS, email through Mailpit, receipt extractor."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.email import Email, MailpitClient, SmtpEmailSender
from app.adapters.extractor import ExtractionFailed, FakeExtractor, image_key
from app.adapters.sms import FakeSms
from app.clock import ControllableClock


async def test_fake_sms_records_and_checks_codes(
    sessions: async_sessionmaker[AsyncSession], clock: ControllableClock
) -> None:
    sms = FakeSms(sessions, clock)
    await sms.start("+12015550142")
    await sms.start("+12015550177")
    [latest] = await sms.sent("+12015550142")
    assert len(latest["code"]) == 6
    assert await sms.check("+12015550142", latest["code"])
    wrong = "000000" if latest["code"] != "000000" else "111111"
    assert not await sms.check("+12015550142", wrong)
    assert len(await sms.sent()) == 2


async def test_fake_extractor_modes(tmp_path: Path, redis: Redis) -> None:
    image = b"\x89PNG fake receipt"
    (tmp_path / f"{image_key([image])}.json").write_text(json.dumps({"store_name": "ShopRite", "items": []}))
    fake = FakeExtractor(tmp_path, redis, hang_seconds=5)
    assert (await fake.extract([image]))["store_name"] == "ShopRite"
    with pytest.raises(ExtractionFailed):
        await fake.extract([b"unknown image"])
    await fake.set_mode("fail")
    with pytest.raises(ExtractionFailed):
        await fake.extract([image])
    await fake.set_mode("hang")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(fake.extract([image]), 0.2)
    # Another process (the worker) sees the mode through Redis.
    assert (await redis.get("test:extractor:mode")) == "hang"


@pytest.fixture(scope="module")
def mailpit() -> Iterator[tuple[str, str]]:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import HttpWaitStrategy

    container = (
        DockerContainer("axllent/mailpit:v1.24")
        .with_exposed_ports(1025, 8025)
        .waiting_for(HttpWaitStrategy(8025, "/api/v1/info"))
    )
    with container:
        host = container.get_container_host_ip()
        yield (
            f"smtp://{host}:{container.get_exposed_port(1025)}",
            f"http://{host}:{container.get_exposed_port(8025)}",
        )


async def test_email_goes_to_mailpit(mailpit: tuple[str, str]) -> None:
    smtp_url, api_url = mailpit
    reader = MailpitClient(api_url)
    await reader.clear()
    sender = SmtpEmailSender(smtp_url, "KitchenTab <hello@example.com>")
    await sender.send(
        Email(
            to="arjun@example.test",
            subject="Pranav split a ShopRite run",
            text="Your share is $33.91.",
            headers={"List-Unsubscribe": "<https://kitchentab.app/u/x>"},
        )
    )
    [summary] = await reader.messages("arjun@example.test")
    full = await reader.message(summary["ID"])
    assert full["Subject"] == "Pranav split a ShopRite run"
    assert "$33.91" in full["Text"]
