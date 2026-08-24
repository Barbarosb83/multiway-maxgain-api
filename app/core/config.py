"""Uygulama ayarları (ortam değişkenlerinden okunur)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MAXGAIN_", env_file=".env", extra="ignore")

    app_name: str = "Multiway MaxGain API"
    version: str = "0.1.0"
    root_path: str = ""
    cors_origins: list[str] = ["*"]
    max_batch_size: int = 100


settings = Settings()
