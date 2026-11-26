from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: Literal["local", "demo", "production"] = "local"
    database_url: str = "sqlite:///./greenlight.db"
    secret_key: SecretStr = SecretStr("local-only-not-secret")
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

    @property
    def secure_cookies(self) -> bool:
        return self.base_url.startswith("https://")


@lru_cache
def get_settings() -> Settings:
    return Settings()
