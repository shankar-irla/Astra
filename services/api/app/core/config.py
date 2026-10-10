from functools import lru_cache

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Astra API"
    environment: str = "development"
    database_url: str = "sqlite:///./astra.db"
    github_api_url: str = "https://api.github.com"
    github_api_version: str = "2022-11-28"
    github_request_timeout_seconds: float = Field(default=10, gt=0, le=60)
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://127.0.0.1:3000"])
    session_cookie_secure: bool = False
    session_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)
    frontend_url: str = "http://localhost:3000"
    github_app_client_id: str | None = None
    github_app_client_secret: SecretStr | None = None
    github_app_callback_url: str | None = None
    token_encryption_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_request_timeout_seconds: float = Field(default=30, gt=0, le=120)

    @model_validator(mode="after")
    def require_secure_production_cookies(self) -> "Settings":
        if self.environment.lower() in {"production", "prod"} and not self.session_cookie_secure:
            raise ValueError("SESSION_COOKIE_SECURE must be true in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
