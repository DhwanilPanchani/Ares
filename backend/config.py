"""
Configuration for Project Ares.

All settings are loaded from environment variables (or a .env file).
Access the singleton via: from backend.config import settings
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Ollama
    # ------------------------------------------------------------------
    ollama_base_url: str = Field(default="http://localhost:11434")
    ollama_keep_alive: str = Field(default="1m")
    ollama_orchestrator_model: str = Field(default="qwen2.5:3b")
    ollama_critic_model: str = Field(default="phi4-mini")
    ollama_embed_model: str = Field(default="nomic-embed-text")

    # ------------------------------------------------------------------
    # LangSmith
    # ------------------------------------------------------------------
    langchain_tracing_v2: bool = Field(default=False)
    langchain_api_key: str = Field(default="")
    langchain_project: str = Field(default="ares")

    # ------------------------------------------------------------------
    # Database & Storage
    # ------------------------------------------------------------------
    database_url: str = Field(default="sqlite+aiosqlite:///./ares.db")
    chroma_persist_dir: Path = Field(default=Path("./chroma_db"))
    output_dir: Path = Field(default=Path("./output"))

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    log_level: str = Field(default="INFO")

    # ------------------------------------------------------------------
    # Frontend
    # ------------------------------------------------------------------
    next_public_api_url: str = Field(default="http://localhost:8000")

    @property
    def db_path(self) -> Path:
        """Return the SQLite file path extracted from DATABASE_URL."""
        # e.g. sqlite+aiosqlite:///./ares.db  →  Path("./ares.db")
        raw = self.database_url
        if ":///" in raw:
            path_str = raw.split("///", 1)[1]
            return Path(path_str)
        return Path("ares.db")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


# Convenience singleton imported across the codebase
settings: Settings = get_settings()
