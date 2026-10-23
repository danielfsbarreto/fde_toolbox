"""Environment-driven settings. Secrets come from env vars (AMP deployment env or Secrets Manager)."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_REPORT_LLM = "anthropic/claude-opus-5-5"  # a report is read by others: the strongest writer


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    mongodb_connection_string: SecretStr
    mongodb_database: str = "fde_activity_log"
    report_llm_model: str = Field(default=DEFAULT_REPORT_LLM, description="Writes the sections and the overview")
    todoist_api_key: SecretStr | None = Field(default=None, description="For the follow-ups' Todoist status")
    section_max_chars: int = Field(default=80_000, description="Text budget for one account's days in one call")
