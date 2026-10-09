"""Derived link tokens (PRD §8, ADR-0018).

Tokens are 128-bit, base62 (22 chars), and only their peppered hash is stored. To let members
see the invite link again (House screen) without storing it, a token is derived:
token = base62(HMAC-SHA256(pepper, kind:id:generation))[:22], where `generation` changes on
rotation. Lookup is by the stored hash; comparison is constant-time by construction.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime

from app.tokens import BASE62, TOKEN_LENGTH, hash_token


def derive_token(pepper: str, kind: str, object_id: uuid.UUID, generation: datetime | str) -> str:
    gen = generation.isoformat() if isinstance(generation, datetime) else generation
    digest = hmac.new(pepper.encode(), f"derive:{kind}:{object_id}:{gen}".encode(), hashlib.sha256).digest()
    value = int.from_bytes(digest[:17], "big")  # 136 bits >= 128 bits of entropy kept
    chars = []
    for _ in range(TOKEN_LENGTH):
        value, rem = divmod(value, 62)
        chars.append(BASE62[rem])
    return "".join(chars)


def invite_token(pepper: str, house_id: uuid.UUID, generation: datetime) -> tuple[str, str]:
    """(token, stored hash) for a house invite link."""
    token = derive_token(pepper, "invite", house_id, generation)
    return token, hash_token(token, pepper)


def bill_token(pepper: str, bill_id: uuid.UUID, generation: datetime) -> tuple[str, str]:
    token = derive_token(pepper, "bill", bill_id, generation)
    return token, hash_token(token, pepper)


def looks_like_token(value: str) -> bool:
    return len(value) == TOKEN_LENGTH and all(ch in BASE62 for ch in value)
