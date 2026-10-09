"""Link tokens (PRD §8): 128-bit random, base62 (22 chars), stored as a peppered hash, compared in
constant time. Full link handling and LINK-01 tests land in Milestone 3."""

from __future__ import annotations

import hashlib
import hmac
import secrets

BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
TOKEN_LENGTH = 22  # 62**22 > 2**128


def new_token() -> str:
    value = secrets.randbits(128)
    chars = []
    for _ in range(TOKEN_LENGTH):
        value, rem = divmod(value, 62)
        chars.append(BASE62[rem])
    return "".join(chars)


def hash_token(token: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()


def token_matches(token: str, stored_hash: str, pepper: str) -> bool:
    return hmac.compare_digest(hash_token(token, pepper), stored_hash)
