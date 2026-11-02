from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.contracts import MAX_INPUT_LENGTH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EMORA_", env_file=".env", extra="ignore")

    model: str = ""
    api_key: SecretStr = SecretStr("EMPTY")
    api_base: str | None = None
    database_url: str = "sqlite:///backend/data/emora.db"
    request_timeout_seconds: float = Field(default=90, gt=0)
    max_input_length: int = Field(default=MAX_INPUT_LENGTH, gt=0)
    qwen_guard_base_url: str = "http://127.0.0.1:8001/v1"
    qwen_guard_api_key: SecretStr = SecretStr("EMPTY")
    qwen_guard_model: str = "Qwen/Qwen3Guard-Gen-4B"
    qwen_guard_timeout_seconds: float = Field(default=15, gt=0)

    @field_validator("max_input_length")
    @classmethod
    def input_limit_cannot_exceed_contract_cap(cls, value: int) -> int:
        if value > MAX_INPUT_LENGTH:
            raise ValueError("max_input_length exceeds contract cap")
        return value



def get_config_summary(settings: Settings) -> dict:
    return {
        "analysis_model": settings.model,
        "analysis_api_base": settings.api_base,
        "database_url_configured": bool(settings.database_url),
        "request_timeout_seconds": settings.request_timeout_seconds,
        "qwen_guard_base_url": settings.qwen_guard_base_url,
        "qwen_guard_model": settings.qwen_guard_model,
        "qwen_guard_timeout_seconds": settings.qwen_guard_timeout_seconds,
        "secret_values_exposed": False,
    }
