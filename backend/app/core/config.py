from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
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
    app_build_revision: str = "unknown"
    app_runtime_role: Literal["api", "worker"] = "api"
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

    # Public hackathon demo. The mode remains disabled unless the operator
    # supplies every delivery and origin setting below.
    demo_public_mode: bool = False
    demo_public_origin: str = ""
    demo_otp_hmac_key: str = ""
    mailgun_api_key: str = Field(default="", repr=False)
    mailgun_domain: str = ""
    mailgun_sender_email: str = ""
    mailgun_region: Literal["us", "eu"] = "us"
    demo_global_search_limit: int = Field(default=40, ge=1, le=10000)

    # RQ
    rq_queue_name: str = "default"
    rq_job_timeout_seconds: int = 1800
    sales_playbook_test_execution: bool = False
    sales_playbook_test_provider: str = ""

    # File storage
    storage_endpoint: str = "http://localhost:9000"
    storage_access_key: str = "minioadmin"
    storage_secret_key: str = "minioadmin"
    storage_bucket: str = "tuwebmx-crm"
    storage_region: str = "us-east-1"

    # External providers
    google_places_api_key: str = ""
    google_places_max_results: int = Field(default=60, ge=1, le=60)
    google_geocoding_api_key: str = ""
    meta_access_token: str = ""
    openai_api_key: str = ""
    llm_api_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    nebius_enabled: bool = False
    nebius_api_key: str = ""
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1"
    nebius_model: str = "nvidia/nemotron-3-super-120b-a12b"
    nebius_ai_project_id: str = ""
    nebius_timeout_seconds: float = 60.0
    # Nebius documents 8192 as the default max_tokens value when omitted.
    # Keep the diagnostic budget explicit and bounded at that documented value.
    nebius_max_completion_tokens: int = Field(default=8192, ge=1, le=8192)
    # Pin the production v3 extraction profile independently of global Nebius
    # defaults, which remain in use by existing non-v3 workloads.
    sales_playbook_llm_model: Literal["Qwen/Qwen3-30B-A3B-Instruct-2507"] = (
        "Qwen/Qwen3-30B-A3B-Instruct-2507"
    )
    sales_playbook_llm_timeout_seconds: float = Field(default=120.0, ge=5.0, le=120.0)
    sales_playbook_llm_max_completion_tokens: int = Field(default=4096, ge=1, le=4096)
    sales_playbook_max_provider_attempts: int = Field(default=24, ge=1, le=1000)
    sales_playbook_max_operations: int = Field(default=24, ge=1, le=1000)
    sales_playbook_max_dynamic_operations: int = Field(default=3, ge=0, le=1000)
    sales_playbook_execution_deadline_seconds: int = Field(default=3180, ge=1, le=86400)
    sales_playbook_closeout_reserve_seconds: int = Field(default=300, ge=1, le=3600)
    sales_playbook_rq_termination_grace_seconds: int = Field(default=30, ge=1, le=600)
    tavily_enabled: bool = False
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    tavily_timeout_seconds: float = Field(default=20.0, ge=5.0, le=60.0)
    tavily_search_depth: Literal["basic", "advanced"] = "basic"
    tavily_max_results: int = Field(default=5, ge=1, le=20)
    tavily_max_extract_urls: int = Field(default=1, ge=0, le=3)
    tavily_cache_hours: int = Field(default=24, ge=0, le=720)
    embedding_model: str = "text-embedding-3-small"
    transcription_api_url: str = "https://api.openai.com/v1/audio/transcriptions"
    transcription_api_key: str = ""
    transcription_model: str = "whisper-1"

    # Lead Fit / Jev — deshabilitado por defecto. Requiere gate operativo independiente.
    lead_fit_enabled: bool = False
    jev_enabled: bool = False
    openrouter_api_key: str = ""
    jev_model: str = "typesafe/jev-1.14"
    jev_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    jev_max_request_bytes: int = Field(default=65536, ge=1024, le=1048576)
    jev_authorized_models: tuple[str, ...] = ("typesafe/jev-1.14",)

    @model_validator(mode="after")
    def validate_sales_playbook_execution_budget(self):
        if self.sales_playbook_max_provider_attempts > self.sales_playbook_max_operations:
            raise ValueError("SalesPlaybook provider attempts cannot exceed the operation budget")
        if self.sales_playbook_max_dynamic_operations > self.sales_playbook_max_operations:
            raise ValueError("SalesPlaybook dynamic operations cannot exceed the operation budget")
        if self.sales_playbook_closeout_reserve_seconds >= self.sales_playbook_execution_deadline_seconds:
            raise ValueError("SalesPlaybook closeout reserve must be below the total deadline")
        required_seconds = (
            self.sales_playbook_max_provider_attempts * self.sales_playbook_llm_timeout_seconds
            + self.sales_playbook_closeout_reserve_seconds
        )
        if self.sales_playbook_execution_deadline_seconds < required_seconds:
            raise ValueError("SalesPlaybook deadline cannot accommodate its attempt budget and closeout reserve")
        return self

    @model_validator(mode="after")
    def validate_public_demo_configuration(self):
        if not self.demo_public_mode:
            return self
        origin = urlsplit(self.demo_public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.path
                or origin.query or origin.fragment or origin.username or origin.password):
            raise ValueError("public demo origin must be an exact HTTPS origin without path or credentials")
        if self.app_runtime_role == "api":
            if len(self.demo_otp_hmac_key.encode("utf-8")) < 32:
                raise ValueError("DEMO_OTP_HMAC_KEY must contain at least 32 bytes")
            from app.modules.demo.mail import DemoMailConfig
            DemoMailConfig(
                domain=self.mailgun_domain, api_key=self.mailgun_api_key,
                from_address=self.mailgun_sender_email, region=self.mailgun_region,
            )
        if not self.cookie_secure or self.cookie_samesite.lower() != "lax":
            raise ValueError("public demo requires Secure and SameSite=Lax cookies")
        if self.demo_public_origin not in self.cors_origins:
            raise ValueError("public demo origin must be listed as an exact CORS origin")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
