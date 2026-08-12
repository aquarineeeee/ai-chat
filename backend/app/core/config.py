from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str
    app_env: str
    app_host: str
    app_port: int
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    jwt_secret: str
    jwt_algorithm: str
    jwt_expire_days: int
    login_password_hash: str
    key_encryption_secret: str
    default_provider: str
    default_model: str
    default_temperature: float
    default_max_tokens: int
    memory_enabled: bool
    memory_mcp_url: str
    memory_timeout_seconds: float
    memory_write_timeout_seconds: float
    memory_max_context_chars: int
    memory_write_max_chars: int
    approval_required_tools: tuple[str, ...]

    @property
    def cookie_secure(self) -> bool:
        return self.app_env.lower() == "production"

    @property
    def async_database_url(self) -> str:
        password = quote_plus(self.db_password)
        return f"mysql+aiomysql://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"

    @property
    def sync_database_url(self) -> str:
        password = quote_plus(self.db_password)
        return f"mysql+pymysql://{self.db_user}:{password}@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"

    @property
    def mysql_connect_args(self) -> dict[str, str]:
        """Configure every MySQL session to evaluate server timestamps in UTC."""
        return {"init_command": "SET time_zone = '+00:00'"}


def _required(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_tuple(name: str) -> tuple[str, ...]:
    value = os.getenv(name, "")
    if not value.strip():
        return ()
    items = [item.strip() for item in value.split(",")]
    return tuple(item for item in items if item)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "ai-chat"),
        app_env=os.getenv("APP_ENV", "development"),
        app_host=os.getenv("APP_HOST", "127.0.0.1"),
        app_port=int(os.getenv("APP_PORT", "10000")),
        db_host=_required("DB_HOST", "127.0.0.1"),
        db_port=int(os.getenv("DB_PORT", "3306")),
        db_name=_required("DB_NAME", "ai_chat"),
        db_user=_required("DB_USER", "aichat"),
        db_password=_required("DB_PASSWORD", "change_me"),
        jwt_secret=_required("JWT_SECRET", "dev-only-secret"),
        jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
        jwt_expire_days=int(os.getenv("JWT_EXPIRE_DAYS", "7")),
        login_password_hash=_required("LOGIN_PASSWORD_HASH", "$2b$12$invalid.invalid.invalid.invalid.invalid.invalid"),
        key_encryption_secret=_required("KEY_ENCRYPTION_SECRET", "dev-only-key-secret"),
        default_provider=os.getenv("DEFAULT_PROVIDER", "openai"),
        default_model=os.getenv("DEFAULT_MODEL", "gpt-4.1-mini"),
        default_temperature=float(os.getenv("DEFAULT_TEMPERATURE", "0.7")),
        default_max_tokens=int(os.getenv("DEFAULT_MAX_TOKENS", "2000")),
        memory_enabled=_bool("MEMORY_ENABLED", False),
        memory_mcp_url=os.getenv("MEMORY_MCP_URL", "http://127.0.0.1:8001/mcp"),
        memory_timeout_seconds=float(os.getenv("MEMORY_TIMEOUT_SECONDS", "5")),
        memory_write_timeout_seconds=float(os.getenv("MEMORY_WRITE_TIMEOUT_SECONDS", "15")),
        memory_max_context_chars=int(os.getenv("MEMORY_MAX_CONTEXT_CHARS", "3000")),
        memory_write_max_chars=int(os.getenv("MEMORY_WRITE_MAX_CHARS", "6000")),
        approval_required_tools=_csv_tuple("APPROVAL_REQUIRED_TOOLS"),
    )
