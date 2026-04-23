from functools import lru_cache
from urllib.parse import quote_plus
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Financa"
    app_env: str = "development"
    secret_key: str = "change-me"
    database_url: str = "postgresql+psycopg://financa:financa@db:5432/financa"
    pghost: str = ""
    pgport: str = ""
    pguser: str = ""
    pgpassword: str = ""
    pgdatabase: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    admin_email: str = "admin@local.test"
    admin_password: str = "admin123"
    admin_name: str = "Administrador"
    upload_dir: str = "app/static/uploads"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    app_base_url: str = "http://localhost:8000"
    groq_api_key: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def upload_path(self) -> Path:
        return Path(self.upload_dir)

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url.strip():
            return self.database_url.strip()
        if all([self.pghost, self.pgport, self.pguser, self.pgpassword, self.pgdatabase]):
            password = quote_plus(self.pgpassword)
            return f"postgresql+psycopg://{self.pguser}:{password}@{self.pghost}:{self.pgport}/{self.pgdatabase}"
        raise RuntimeError(
            "Database configuration is missing. Set DATABASE_URL or the Railway PGHOST/PGPORT/PGUSER/PGPASSWORD/PGDATABASE variables."
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
