"""Environment configuration, validated at startup (PRD §12: a missing secret fails boot).

Fake providers are allowed only in `local` and `test`; `/__test/*` routes only in `test` with
`TEST_ROUTES_ENABLED=true` (TEST_PLAN §2, SEC-11). Every problem is reported at once.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

AppEnv = Literal["local", "test", "staging", "prod"]
PLACEHOLDER_PREFIX = "change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"),),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # App
    app_env: AppEnv = "local"
    web_origin: str = "http://localhost:3000"
    api_origin: str = "http://localhost:8000"
    public_link_base: str = "http://localhost:3000"
    session_secret: SecretStr
    token_hash_pepper: SecretStr
    log_level: str = "INFO"

    # Database / cache
    database_url: str
    redis_url: str

    # Object storage
    s3_endpoint: str | None = None
    s3_bucket: str = "kitchentab-receipts"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "us-east-1"

    # Google OIDC. In local/test the issuer is the in-repo mock (infra/docker-compose.yml).
    google_issuer: str = "https://accounts.google.com"
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:3000/auth/google/callback"

    # Email
    email_provider: Literal["fake", "resend", "postmark", "ses"] = "fake"
    email_from: str = "KitchenTab <hello@example.com>"
    resend_api_key: SecretStr | None = None
    smtp_url: str = "smtp://localhost:1025"

    # SMS
    sms_provider: Literal["fake", "twilio_verify"] = "fake"
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_verify_service_sid: str | None = None

    # Bot protection
    turnstile_site_key: str | None = None
    turnstile_secret_key: SecretStr | None = None

    # Receipt extraction
    extractor_provider: str = "fake"
    extractor_api_key: SecretStr | None = None
    extractor_model: str | None = None
    extractor_daily_cap_per_house: int = Field(default=30, ge=1)

    # Web Push
    push_provider: Literal["fake", "webpush"] = "fake"
    vapid_public_key: str | None = None
    vapid_private_key: SecretStr | None = None
    vapid_subject: str = "mailto:hello@example.com"
    fake_push_url: str = "http://localhost:4010"

    # Test-only
    test_routes_enabled: bool = False

    # Observability
    sentry_dsn: str | None = None
    posthog_key: str | None = None
    posthog_host: str | None = None

    @property
    def is_deployed(self) -> bool:
        return self.app_env in ("staging", "prod")

    @property
    def test_routes_active(self) -> bool:
        return self.app_env == "test" and self.test_routes_enabled

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        problems: list[str] = []

        if self.test_routes_enabled and self.app_env != "test":
            problems.append("TEST_ROUTES_ENABLED is only allowed when APP_ENV=test")

        if self.is_deployed:
            for name in ("session_secret", "token_hash_pepper"):
                value = getattr(self, name).get_secret_value()
                if value.startswith(PLACEHOLDER_PREFIX) or len(value) < 32:
                    problems.append(f"{name.upper()} must be a real secret of at least 32 characters")
            fakes = {
                "EMAIL_PROVIDER": self.email_provider,
                "SMS_PROVIDER": self.sms_provider,
                "EXTRACTOR_PROVIDER": self.extractor_provider,
                "PUSH_PROVIDER": self.push_provider,
            }
            problems += [
                f"{k}=fake is not allowed in {self.app_env}" for k, v in fakes.items() if v == "fake"
            ]
            if (
                "localhost" in self.google_issuer
                or not self.google_client_id
                or not self.google_client_secret
            ):
                problems.append("Google OIDC needs a real issuer, GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET")
            if not self.turnstile_secret_key:
                problems.append("TURNSTILE_SECRET_KEY is required")

        required_by_provider: list[tuple[bool, str, object]] = [
            (self.email_provider == "resend", "RESEND_API_KEY", self.resend_api_key),
            (self.sms_provider == "twilio_verify", "TWILIO_ACCOUNT_SID", self.twilio_account_sid),
            (self.sms_provider == "twilio_verify", "TWILIO_AUTH_TOKEN", self.twilio_auth_token),
            (
                self.sms_provider == "twilio_verify",
                "TWILIO_VERIFY_SERVICE_SID",
                self.twilio_verify_service_sid,
            ),
            (self.extractor_provider != "fake", "EXTRACTOR_API_KEY", self.extractor_api_key),
            (self.extractor_provider != "fake", "EXTRACTOR_MODEL", self.extractor_model),
            (self.push_provider == "webpush", "VAPID_PUBLIC_KEY", self.vapid_public_key),
            (self.push_provider == "webpush", "VAPID_PRIVATE_KEY", self.vapid_private_key),
        ]
        problems += [
            f"{name} is required by the configured provider"
            for cond, name, v in required_by_provider
            if cond and not v
        ]

        if problems:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
