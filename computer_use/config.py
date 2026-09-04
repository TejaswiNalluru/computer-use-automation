"""Runtime configuration."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    target_url: str = "http://127.0.0.1:3000/"
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str | None = Field(default=None, alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    mock_llm: bool = Field(default=False, alias="CUA_MOCK_LLM")
    headless: bool = Field(default=True, alias="CUA_HEADLESS")
    max_agent_steps: int = Field(default=12, alias="CUA_MAX_STEPS")
    policy_path: Path = ROOT / "policy" / "allowlist.yaml"
    capabilities_dir: Path = ROOT / "capabilities"
    evidence_dir: Path = ROOT / "evidence"
    handoff_dir: Path = ROOT / "handoff"


def get_settings() -> Settings:
    return Settings()
