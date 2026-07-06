# app/config.py
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://user:password@postgres:5432/construction_db"

    # Redis
    redis_url: str = "redis://redis:6379/0"

    # Auth / JWT
    secret_key: str = "change-me-to-a-long-random-string"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # AWS / S3
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-northeast-1"
    s3_bucket_name: str = "construction-progress-dev"

    # Anthropic
    anthropic_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"

    # Email (app/services/email_service.py) — used for teammate temp
    # credentials and landing page contact form notifications. Leave
    # smtp_user/smtp_password blank for a local SMTP relay that doesn't
    # require auth (e.g. mailhog/mailcatcher in dev).
    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = "noreply@construction-platform.local"
    smtp_use_tls: bool = True

    # Where landing page contact form submissions get emailed to.
    contact_notify_email: str = "contact@construction-platform.local"

    # App
    environment: str = "development"
    cors_origins: str = "*"  # comma-separated list in production

    # When True: POST /api/v1/organizations/members returns the generated
    # temp_password directly in its response (see TeamMemberCreateOut),
    # in addition to emailing it. This is what lets seed.py log new
    # teammates in without a working SMTP server. MUST be False in any
    # real deployment — set via DEBUG=true in .env for local dev only.
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()