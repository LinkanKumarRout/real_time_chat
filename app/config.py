from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Real-Time Notification Service"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = True

    mongodb_url: str = "mongodb://localhost:27017"
    mongodb_db: str = "realtime_chat"

    redis_url: str = "redis://localhost:6379/0"
    redis_channel_prefix: str = "chat"

    cors_origins: str = "*"

    jwt_secret: str = "change-me-in-production-use-a-long-random-secret"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    admin_username: str = "admin"
    admin_password: str = "admin123"
    admin_display_name: str = "Admin"

    @property
    def cors_origin_list(self) -> list[str]:
        if self.cors_origins.strip() == "*":
            return ["*"]
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
