from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    environment: Literal["local", "test", "staging", "production"] = "local"
    version: str = "0.1.0"

    # postgresql+asyncpg://user:password@host:port/db  (add ?ssl=require for Neon)
    database_url: str
    # True when connecting through Neon's pooled endpoint (PgBouncer, transaction mode).
    # asyncpg's prepared-statement cache breaks behind it, so it is switched off.
    database_pooled: bool = False

    # Signs short-lived admin login challenges. Long random string in staging/production.
    secret_key: str = Field(min_length=32)

    # Signs student access tokens. Separate from SECRET_KEY so either can be rotated alone.
    jwt_secret: str = Field(min_length=32)
    # Django's default; must stay 1,000,000 outside tests so hashes match the website's.
    password_hash_iterations: int = Field(1_000_000, ge=1_000)
    access_token_minutes: int = 15
    refresh_token_days: int = 60

    # Browser origins allowed to call /v1 (the Expo web preview locally). Phones don't need CORS.
    cors_origins: list[str] = []

    # Email: "resend" sends for real; "log" writes emails to the log (local development, so test
    # sign-ups with made-up addresses never bounce and hurt the sending domain's reputation).
    email_delivery: Literal["resend", "log"] = "log"
    resend_api_key: str = ""
    resend_from_email: str = ""
    email_from_name: str = "Speak Your Mind"
    # Shown on the Privacy Policy and Terms pages; leave empty to point to the SYM website instead.
    support_email: str = ""
    # Lets an outside scheduler call POST /v1/jobs/run (Authorization: Bearer <secret>); empty turns it off.
    cron_secret: str = ""

    # Outside services; features that need them fall back gracefully when a key is missing.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Push notifications through Expo. The access token is optional (only if "enhanced security" is on).
    push_enabled: bool = True
    expo_access_token: str = ""
    deepgram_api_key: str = ""
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""

    admin_session_hours: int = 12
    admin_max_failed_logins: int = 5
    admin_lockout_minutes: int = 15

    # Refuse to start when the web tables we depend on have changed shape.
    contract_check_on_startup: bool = True

    # How many proxies in front of us append to X-Forwarded-For (Render: 1; local: 0). See core/client_ip.py.
    trusted_proxy_hops: int = 0

    @property
    def is_deployed(self) -> bool:
        return self.environment in ("staging", "production")


@lru_cache
def get_settings() -> Settings:
    return Settings()
