from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    app_name: str = "AI-Hexing"
    app_env: Literal["development", "production", "test"] = "development"
    web_origin: str = "http://127.0.0.1:3060"
    database_url: str = "sqlite:///./data/ai_hexing.db"
    data_dir: Path = ROOT / "data"
    provider_mode: Literal["mock", "live"] = "mock"
    github_token: SecretStr = SecretStr("")
    github_api_base_url: Literal["https://api.github.com"] = "https://api.github.com"
    qwen_api_key: SecretStr = SecretStr("")
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qwen_region: str = "beijing"
    qwen_model: str = "qwen3.7-flash"
    qwen_analysis_thinking: bool = False
    qwen_writing_thinking: bool = False
    voice_api_key: SecretStr = SecretStr("")
    voice_region: str = "beijing"
    voice_target_model: str = "cosyvoice-v3.5-flash"
    voice_enrollment_endpoint: str = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
    voice_synthesis_endpoint: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    voice_sample_allowed_host: str = ""
    provider_contract_version: str = ""
    live_calls_enabled: bool = False
    enable_validation_tools: bool = True
    external_call_timeout_seconds: int = 120
    job_lease_seconds: int = 180
    job_heartbeat_seconds: int = 15
    session_ttl_seconds: int = 28800
    cost_single_limit_micros: int = Field(default=1_000_000, ge=0)
    cost_monthly_limit_micros: int = Field(default=30_000_000, ge=0)
    max_upload_bytes: int = 20 * 1024 * 1024
    public_event_source_per_minute: int = Field(default=60, ge=1, le=600)
    public_event_total_per_minute: int = Field(default=600, ge=1, le=6000)
    public_event_retention_days: int = Field(default=30, ge=1, le=365)
    public_event_max_rows: int = Field(default=20000, ge=1, le=100000)

    @field_validator("data_dir", mode="before")
    @classmethod
    def absolute_data(cls, value):
        p = Path(value)
        return p if p.is_absolute() else ROOT / p

    @field_validator("database_url")
    @classmethod
    def absolute_db(cls, value):
        if value.startswith("sqlite:///./"):
            return "sqlite:///" + str(ROOT / value[len("sqlite:///./") :])
        return value


@lru_cache
def get_settings():
    return Settings()
