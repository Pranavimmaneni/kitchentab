"""Environment configuration, validated at startup (PRD §12: a missing secret fails boot).

Profiles (ADR-0017): `local` uses the compose stack (Redis, arq, MinIO); `vercel` uses the
production adapters (Postgres-only realtime, Vercel Queues, Blob, test inbox). Fake providers are
allowed only in `local` and `test`; `/__test/*` routes only in `test` with `TEST_ROUTES_ENABLED`
(SEC-11). Every problem is reported at once.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

AppEnv = Literal["local", "test", "staging", "prod"]
Profile = Literal["local", "vercel"]
PLACEHOLDER_PREFIX = "change-me"
TEST_INBOX_SECRET_MIN = 64
VERCEL_MAX_BODY_BYTES = 4_500_000  # Hobby request/response body limit (ADR-0011)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(str(REPO_ROOT / ".env"),),
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # `KEY=` in .env means "use the default"
        case_sensitive=False,
    )

    # App
    app_env: AppEnv = "local"
    profile: Profile = "local"
    web_origin: str = "http://localhost:3000"
    public_link_base: str = "http://localhost:3000"
    session_secret: SecretStr
    token_hash_pepper: SecretStr
    cron_secret: SecretStr | None = None
    log_level: str = "INFO"
    max_body_bytes: int = VERCEL_MAX_BODY_BYTES
    # Clients older than this get 426 STALE_CLIENT (PRD §21.6); bump on breaking API changes.
    min_client_version: int = 0

    # Database / cache. Redis is used only with PROFILE=local (ADR-0012, ADR-0014).
    database_url: str
    database_direct_url: str | None = None  # Neon direct URL, migrations only
    redis_url: str | None = None

    # Receipt storage (ADR-0015)
    storage_provider: Literal["s3", "fakeblob", "vercel_blob"] = "s3"
    s3_endpoint: str | None = "http://localhost:9000"
    s3_bucket: str = "kitchentab-receipts"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "us-east-1"
    fake_blob_url: str = "http://localhost:4012"
    blob_read_write_token: SecretStr | None = None

    # Queue for the outbox (ADR-0013). Default follows the profile.
    queue_provider: Literal["arq", "vercel", "inline"] | None = None

    # Google OIDC: shown only when GOOGLE_CLIENT_ID is set (ADR-0016).
    google_issuer: str = "https://accounts.google.com"
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:3000/api/v1/auth/google/callback"

    # Email (ADR-0016): fake = SMTP to Mailpit; smtp = Gmail; testinbox = Postgres + secret.
    email_provider: Literal["fake", "smtp", "testinbox"] = "fake"
    email_from: str = "KitchenTab <hello@example.com>"
    smtp_url: str = "smtp://localhost:1025"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    test_inbox_secret: SecretStr | None = None

    # SMS: shown only when configured (none hides the phone buttons).
    sms_provider: Literal["none", "fake", "twilio_verify"] = "none"
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_verify_service_sid: str | None = None

    # Bot protection (phone codes only)
    turnstile_site_key: str | None = None
    turnstile_secret_key: SecretStr | None = None

    # Receipt extraction (ADR-0010): local needs no key; llm is the optional plugin.
    extractor_provider: Literal["local", "fake", "llm"] = "local"
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

    @property
    def is_deployed(self) -> bool:
        return self.app_env in ("staging", "prod")

    @property
    def test_routes_active(self) -> bool:
        return self.app_env == "test" and self.test_routes_enabled

    @property
    def queue(self) -> str:
        return self.queue_provider or ("vercel" if self.profile == "vercel" else "arq")

    @property
    def use_redis(self) -> bool:
        return self.profile == "local" and self.redis_url is not None

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id)

    @property
    def phone_enabled(self) -> bool:
        return self.sms_provider != "none"

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        problems: list[str] = []

        if self.test_routes_enabled and self.app_env != "test":
            problems.append("TEST_ROUTES_ENABLED is only allowed when APP_ENV=test")
        if self.profile == "local" and not self.redis_url and self.queue == "arq":
            problems.append("REDIS_URL is required with PROFILE=local (arq queue); use PROFILE=vercel without Redis")

        if self.is_deployed:
            for name in ("session_secret", "token_hash_pepper", "cron_secret"):
                secret = getattr(self, name)
                value = secret.get_secret_value() if secret else ""
                if value.startswith(PLACEHOLDER_PREFIX) or len(value) < 32:
                    problems.append(f"{name.upper()} must be a real secret of at least 32 characters")
            if self.profile != "vercel":
                problems.append(f"PROFILE must be vercel in {self.app_env}")
            for key, value in (
                ("EMAIL_PROVIDER", self.email_provider),
                ("SMS_PROVIDER", self.sms_provider),
                ("EXTRACTOR_PROVIDER", self.extractor_provider),
                ("PUSH_PROVIDER", self.push_provider),
            ):
                if value == "fake":
                    problems.append(f"{key}=fake is not allowed in {self.app_env}")
            if self.storage_provider != "vercel_blob":
                problems.append(f"STORAGE_PROVIDER must be vercel_blob in {self.app_env}")
            if self.google_enabled and "localhost" in self.google_issuer:
                problems.append("GOOGLE_ISSUER must be Google's issuer in production")
            for name in ("web_origin", "public_link_base"):
                value = getattr(self, name)
                if not value.startswith("https://") or "localhost" in value:
                    problems.append(f"{name.upper()} must be the https production URL")
            if self.google_enabled and (
                not self.google_redirect_uri.startswith("https://") or "localhost" in self.google_redirect_uri
            ):
                problems.append("GOOGLE_REDIRECT_URI must be the https production callback")
            if self.queue != "vercel":
                problems.append(f"QUEUE_PROVIDER must be vercel in {self.app_env}")

        required: list[tuple[bool, str, object]] = [
            (self.email_provider == "smtp", "SMTP_HOST", self.smtp_host),
            (self.email_provider == "smtp", "SMTP_USER", self.smtp_user),
            (self.email_provider == "smtp", "SMTP_PASSWORD", self.smtp_password),
            (self.sms_provider == "twilio_verify", "TWILIO_ACCOUNT_SID", self.twilio_account_sid),
            (self.sms_provider == "twilio_verify", "TWILIO_AUTH_TOKEN", self.twilio_auth_token),
            (
                self.sms_provider == "twilio_verify",
                "TWILIO_VERIFY_SERVICE_SID",
                self.twilio_verify_service_sid,
            ),
            (self.sms_provider == "twilio_verify", "TURNSTILE_SECRET_KEY", self.turnstile_secret_key),
            (self.google_enabled, "GOOGLE_CLIENT_SECRET", self.google_client_secret),
            (self.extractor_provider == "llm", "EXTRACTOR_API_KEY", self.extractor_api_key),
            (self.extractor_provider == "llm", "EXTRACTOR_MODEL", self.extractor_model),
            (self.push_provider == "webpush", "VAPID_PUBLIC_KEY", self.vapid_public_key),
            (self.push_provider == "webpush", "VAPID_PRIVATE_KEY", self.vapid_private_key),
            (self.storage_provider == "vercel_blob", "BLOB_READ_WRITE_TOKEN", self.blob_read_write_token),
        ]
        problems += [f"{name} is required by the configured provider" for cond, name, v in required if cond and not v]

        if self.email_provider == "testinbox":
            secret = self.test_inbox_secret.get_secret_value() if self.test_inbox_secret else ""
            if len(secret) < TEST_INBOX_SECRET_MIN:
                problems.append(f"TEST_INBOX_SECRET must be at least {TEST_INBOX_SECRET_MIN} characters")

        if problems:
            raise ValueError("Invalid configuration:\n  - " + "\n  - ".join(problems))
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
