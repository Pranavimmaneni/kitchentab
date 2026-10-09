"""Secrets handling: OTP codes, session tokens, CSRF tokens, peppered hashes (PRD §7, §8).

Nothing here logs a value. Hashes use HMAC-SHA256 with TOKEN_HASH_PEPPER so a leaked table is
useless without the pepper.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

from app.tokens import hash_token, new_token

OTP_DIGITS = 6
SESSION_COOKIE = "kt_session"
CSRF_COOKIE = "kt_csrf"
CSRF_HEADER = "X-CSRF-Token"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
US_PHONE_RE = re.compile(r"^\+1[2-9]\d{2}[2-9]\d{6}$")


def new_otp_code() -> str:
    return f"{secrets.randbelow(10**OTP_DIGITS):0{OTP_DIGITS}d}"


def peppered(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def normalize_email(raw: str) -> str | None:
    value = raw.strip().lower()
    return value if EMAIL_RE.match(value) and len(value) <= 254 else None


def normalize_us_phone(raw: str) -> str | None:
    """E.164, US only in v1 (PRD §7)."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "1" + digits
    candidate = f"+{digits}"
    return candidate if US_PHONE_RE.match(candidate) else None


def mask_destination(channel: str, destination: str) -> str:
    """For UI copy ("We sent a 6-digit code to m***@example.com")."""
    if channel == "email":
        local, _, domain = destination.partition("@")
        return f"{local[:1]}***@{domain}"
    return f"(***) ***-{destination[-4:]}"


def new_session_token() -> tuple[str, str]:
    """Returns (cookie value, stored hash)."""
    token = new_token() + new_token()
    return token, hashlib.sha256(token.encode()).hexdigest()


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def csrf_matches(cookie_value: str | None, header_value: str | None) -> bool:
    if not cookie_value or not header_value:
        return False
    return hmac.compare_digest(cookie_value, header_value)


__all__ = [
    "CSRF_COOKIE",
    "CSRF_HEADER",
    "SESSION_COOKIE",
    "csrf_matches",
    "hash_token",
    "mask_destination",
    "new_csrf_token",
    "new_otp_code",
    "new_session_token",
    "new_token",
    "normalize_email",
    "normalize_us_phone",
    "peppered",
    "session_hash",
]
