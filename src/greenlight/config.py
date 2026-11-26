from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "local-only-not-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "demo", "production"] = "local"
    database_url: str = "sqlite:///./greenlight.db"
    database_url_unpooled: str = ""
    secret_key: SecretStr = SecretStr(DEFAULT_SECRET)
    base_url: str = "http://localhost:8000"

    sign_in: Literal["demo", "entra"] = "demo"
    demo_passcode: SecretStr = SecretStr("")
    entra_tenant_id: str = ""
    entra_client_id: str = ""
    entra_client_secret: SecretStr = SecretStr("")

    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    mail_from: str = "greenlight@orvane.example"
    teams_webhook_url: SecretStr = SecretStr("")

    cron_token: SecretStr = SecretStr("")
    reminder_after_hours: int = 24
    max_upload_mb: int = 10
    invoice_reads_per_hour: int = 30

    @model_validator(mode="after")
    def _refuse_unsafe_settings(self) -> "Settings":
        secret = self.secret_key.get_secret_value()
        weak_secret = secret == DEFAULT_SECRET or len(secret) < 32
        if weak_secret and not self.is_local:
            raise ValueError("Set SECRET_KEY to a random value of at least 32 characters.")
        if self.environment != "production":
            return self
        if self.sign_in == "demo":
            raise ValueError("Demo sign-in cannot be used in production. Set SIGN_IN=entra.")
        if self.sign_in == "entra" and not self.entra_tenant_id:
            raise ValueError("Set ENTRA_TENANT_ID so only Orvane accounts can sign in.")
        if not self.base_url.startswith("https://"):
            raise ValueError("BASE_URL must be https in production.")
        return self

    @property
    def migration_database_url(self) -> str:
        """Migrations bypass the connection pooler when a direct URL is given."""
        return self.database_url_unpooled or self.database_url

    @property
    def is_local(self) -> bool:
        host = urlsplit(self.base_url).hostname or ""
        return host in {"localhost", "127.0.0.1", "testserver"}

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def max_body_bytes(self) -> int:
        return (self.max_upload_mb * 5 + 1) * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
