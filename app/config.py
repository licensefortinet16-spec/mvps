from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Financa"
    app_env: str = "development"
    secret_key: str = "change-me"
    database_url: str = "postgresql+psycopg://financa:financa@db:5432/financa"
    google_client_id: str = ""
    google_client_secret: str = ""
    admin_email: str = "admin@local.test"
    admin_password: str = "admin123"
    admin_name: str = "Administrador"
    upload_dir: str = "app/static/uploads"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def upload_path(self) -> Path:
        return Path(self.upload_dir)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
