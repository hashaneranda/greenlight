from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SECRET = "local-only-not-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "demo", "production"] = "local"
    database_url: str = "sqlite:///./greenlight.db"
    secret_key: SecretStr = SecretStr(DEFAULT_SECRET)
    base_url: str = "http://localhost:8000"

    sign_in: Literal["demo", "entra"] = "demo"
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

    @model_validator(mode="after")
    def _refuse_unsafe_production(self) -> "Settings":
        if self.environment != "production":
            return self
        if self.sign_in == "demo":
            raise ValueError("Demo sign-in cannot be used in production. Set SIGN_IN=entra.")
        secret = self.secret_key.get_secret_value()
        if secret == DEFAULT_SECRET or len(secret) < 32:
            raise ValueError("Set SECRET_KEY to a random value of at least 32 characters.")
        if not self.base_url.startswith("https://"):
            raise ValueError("BASE_URL must be https in production.")
        return self

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
