from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # App
    app_name: str = "Tu Web MX CRM"
    environment: str = "development"
    api_prefix: str = "/api/v1"

    # Database
    database_url: str = Field(
        default="postgresql+psycopg://postgres:postgres@localhost:5432/tuwebmx_crm",
        description="PostgreSQL connection URL",
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )

    # Security
    secret_key: str = Field(
        default="dev-secret-key-change-in-production",
        description="Secret key for JWT signing",
    )
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7
    csrf_token_expire_minutes: int = 60

    # CORS
    cors_origins: list[str] = Field(
        default=["http://localhost:5173", "http://localhost:3000"],
        description="Allowed CORS origins",
    )

    # Cookie settings
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    # RQ
    rq_queue_name: str = "default"
    rq_job_timeout_seconds: int = 1800

    # File storage
    storage_endpoint: str = "http://localhost:9000"
    storage_access_key: str = "minioadmin"
    storage_secret_key: str = "minioadmin"
    storage_bucket: str = "tuwebmx-crm"
    storage_region: str = "us-east-1"

    # External providers
    google_places_api_key: str = ""
    google_geocoding_api_key: str = ""
    meta_access_token: str = ""
    openai_api_key: str = ""
    llm_api_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    transcription_api_url: str = "https://api.openai.com/v1/audio/transcriptions"
    transcription_api_key: str = ""
    transcription_model: str = "whisper-1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
