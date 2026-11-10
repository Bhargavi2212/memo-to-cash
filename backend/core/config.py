"""App settings from the environment (and local `.env`), with the sandbox guard.

Importing this module builds `settings`; it raises if PayPal isn't the sandbox, so the
app refuses to start against live PayPal (CLAUDE.md invariant 6).
"""

import os
from typing import Any

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
APP_ENVS = ("dev", "prod", "test")


class Settings(BaseSettings):
    # extra="ignore": local .env also holds keys only the frontend uses (NEXT_PUBLIC_*).
    # hide_input_in_errors: a validation error must never echo .env values (secrets) to logs.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    # Required at startup (P0)
    paypal_base_url: str
    app_env: str
    database_url: SecretStr

    # Optional: checked where they're used
    database_url_unpooled: SecretStr | None = None  # Alembic only
    test_database_url: SecretStr | None = None  # integration tests (P1+)
    paypal_client_id: str | None = None
    paypal_client_secret: SecretStr | None = None
    paypal_business_email: str | None = None
    paypal_webhook_id: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str = "Llama-4-Maverick-17B-128E-Instruct-FP8"
    admin_token: SecretStr | None = None
    demo_mode: bool = False
    demo_cookie_secret: SecretStr | None = None
    ip_hash_secret: SecretStr | None = None
    app_base_url: str | None = None  # the web address; no fallback (P0.md step 3)
    api_public_url: str | None = None  # the api's own public address (PayPal webhook, P3)
    sandbox_buyer_email: str | None = None
    sandbox_buyer_password: SecretStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _blank_is_unset(cls, data: Any) -> Any:
        # `KEY=` in .env or the dashboard means "not set", not an empty secret.
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not (isinstance(v, str) and v.strip() == "")}
        return data

    @field_validator("paypal_base_url", "app_base_url", "api_public_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str | None) -> str | None:
        return v.strip().rstrip("/") if isinstance(v, str) else v

    @field_validator("paypal_base_url")
    @classmethod
    def _sandbox_only(cls, v: str) -> str:
        if v != SANDBOX_BASE_URL:
            raise ValueError(f"PAYPAL_BASE_URL must be the sandbox ({SANDBOX_BASE_URL})")
        return v

    @field_validator("app_env")
    @classmethod
    def _known_env(cls, v: str) -> str:
        if v not in APP_ENVS:
            raise ValueError(f"APP_ENV must be one of {', '.join(APP_ENVS)}")
        return v


def load_settings(**overrides: Any) -> Settings:
    """Build settings. Under APP_ENV=test the local .env is never read."""
    if "_env_file" not in overrides and os.environ.get("APP_ENV") == "test":
        overrides["_env_file"] = None
    return Settings(**overrides)


def sqlalchemy_url(url: str) -> str:
    """Point SQLAlchemy at psycopg v3 (psycopg2 isn't installed). Keeps host, auth and query."""
    for prefix in ("postgresql+psycopg://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    raise ValueError("DATABASE_URL must be a postgres:// or postgresql:// URL")


settings = load_settings()
