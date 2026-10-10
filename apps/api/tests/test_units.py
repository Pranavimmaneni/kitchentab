"""Small units: UUIDv7, clock, token hashing, log redaction, problem details."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

from app.clock import ControllableClock
from app.errors import ErrorCode, Problem
from app.ids import uuid7
from app.logs import JsonFormatter, redact, redact_text
from app.tokens import TOKEN_LENGTH, hash_token, new_token, token_matches
from tests.harness.tags import fr, tid


def test_uuid7_version_variant_and_order() -> None:
    ids = [uuid7() for _ in range(5000)]
    assert all(u.version == 7 and u.variant == "specified in RFC 4122" for u in ids)
    assert ids == sorted(ids, key=lambda u: u.int), "ids from one process sort in creation order"
    assert len(set(ids)) == len(ids)


def test_controllable_clock_pin_advance_and_share() -> None:
    clock = ControllableClock()
    pinned = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
    clock.set(pinned)
    clock.advance(timedelta(hours=48))
    assert clock.now() == pinned + timedelta(hours=48)
    other = ControllableClock()
    other.load(clock.dump())
    assert other.now() == clock.now()
    clock.reset()
    assert abs((clock.now() - datetime.now(UTC)).total_seconds()) < 5
    with pytest.raises(ValueError, match="timezone"):
        clock.set(datetime(2026, 1, 1))  # noqa: DTZ001


def test_tokens_are_128_bit_base62_and_hashed() -> None:
    tokens = {new_token() for _ in range(1000)}
    assert len(tokens) == 1000
    assert all(len(t) == TOKEN_LENGTH and t.isalnum() for t in tokens)
    t = new_token()
    stored = hash_token(t, "pepper")
    assert stored != t and token_matches(t, stored, "pepper")
    assert not token_matches(t, stored, "other-pepper")


def test_redaction_masks_secrets_codes_contacts() -> None:
    text = redact_text("code 123456 sent to meera@example.com and +1 (201) 555-0142 tok k7Q2xR9pVw3LmZt8Hc5NbY")
    assert "123456" not in text and "meera@" not in text and "555-0142" not in text and "k7Q2xR9p" not in text
    assert "m***@example.com" in text
    assert redact({"session_token": "abc", "nested": {"push_endpoint": "x"}, "count": 3}) == {
        "session_token": "[redacted]",
        "nested": {"push_endpoint": "[redacted]"},
        "count": 3,
    }


def test_json_log_line_never_contains_the_code() -> None:
    record = logging.LogRecord("app", logging.INFO, __file__, 1, "otp sent to %s", ("a@b.co",), None)
    record.code = "987654"
    line = json.loads(JsonFormatter().format(record))
    assert "987654" not in json.dumps(line) and "a@b.co" not in line["msg"]


def test_problem_body_shape() -> None:
    body = Problem(409, ErrorCode.LAST_PERSON, "Someone has to keep it", extra={"item_id": "x"}).body("/api/v1/x")
    assert body["code"] == "LAST_PERSON" and body["status"] == 409 and body["item_id"] == "x"
    assert body["type"].endswith("/last-person")


@tid("KITCH-05")
@fr("FR-65")
@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("Whole milk 1 gal", "Whole milk"),
        ("Korean pear box", "Korean pear"),
        ("Organic baby spinach", "Baby spinach"),
        ("eggs 18 ct", "Eggs"),
        ("Kimchi", "Kimchi"),
        ("Dal from Sunday", "Dal"),
        ("Cake for Kabir", "Cake"),
        ("Supercalifragilisticexpialidocious", "Supercalifrag…"),
    ],
)
def test_tile_labels_keep_the_thing_itself(name: str, label: str) -> None:
    from app.domain.kitchen import SHORT_NAME_MAX, short_name

    assert short_name(name) == label and len(short_name(name)) <= SHORT_NAME_MAX
