"""环境变量与用户工作区配置的加载及校验。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages_py.schemas import Contract


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+psycopg://laosun@localhost:5432/postgres"
    checkpoint_url: str | None = None
    database_schema: str = "app"
    checkpoint_schema: str = "checkpoint"
    model_provider: str | None = None
    model_name: str | None = None
    model_endpoint: str | None = None
    model_api_key: str | None = None
    model_timeout_seconds: int = Field(default=60, gt=0)
    model_max_attempts: int = Field(default=2, ge=1, le=2)
    clarification_max_rounds: int = Field(default=2, ge=0, le=2)
    output_max_revisions: int = Field(default=1, ge=0, le=1)
    events_path: Path | None = None
    reference_retrieval_enabled: bool = False
    asset_storage_root: Path | None = None
    service_ttl_seconds: int | None = Field(default=None, gt=0)
    token_ttl_seconds: int | None = Field(default=None, gt=0)
    asset_ttl_seconds: int | None = Field(default=None, gt=0)
    checkpoint_ttl_seconds: int | None = Field(default=None, gt=0)
    deletion_receipt_ttl_seconds: int | None = Field(default=None, gt=0)
    backup_ttl_seconds: int | None = Field(default=None, gt=0)

    def validate_retention(self) -> None:
        values = [self.service_ttl_seconds, self.token_ttl_seconds, self.asset_ttl_seconds,
                  self.checkpoint_ttl_seconds, self.deletion_receipt_ttl_seconds,
                  self.backup_ttl_seconds]
        if any(value is None for value in values):
            raise ValueError("retention policy is required")
        if any(value > self.service_ttl_seconds for value in values[1:]):
            raise ValueError("retention TTL cannot exceed service TTL")

    @field_validator("database_schema", "checkpoint_schema")
    @classmethod
    def valid_schema(cls, value: str) -> str:
        if not value.replace("_", "").isalnum():
            raise ValueError("schema names may contain letters, digits, and underscores")
        return value


class WorkspaceModelConfig(Contract):
    config_version: str
    provider: str
    model_name: str
    endpoint: str | None = None
    credential_ref: str
    timeout_seconds: int = Field(gt=0)
    max_attempts: int = Field(ge=1, le=2)
    clarification_max_rounds: int = Field(ge=0, le=2)
    output_max_revisions: int = Field(ge=0, le=1)


def workspace_id(cwd: Path | None = None) -> str:
    """为工作区分配稳定标识，相关配置与凭据保存在仓库之外。"""
    root = (cwd or Path.cwd()).resolve()
    directory = Path.home() / ".config" / "emotional-agent"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    index_path = directory / "workspaces.json"
    try:
        index = json.loads(index_path.read_text())
    except FileNotFoundError:
        index = {}
    key = str(root)
    if key not in index:
        index[key] = str(uuid4())
        atomic_json(index_path, index)
    workspace_dir = directory / "workspaces" / index[key]
    workspace_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return index[key]


def atomic_json(path: Path, value: dict) -> None:
    """以受限文件权限和原子替换方式保存私有配置。"""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
