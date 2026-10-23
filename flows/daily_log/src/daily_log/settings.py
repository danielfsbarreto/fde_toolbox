"""Environment-driven settings. Secrets come from env vars (AMP deployment env or Secrets Manager)."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_LLM = "anthropic/claude-sonnet-5-5"
DEFAULT_REVIEW_LLM = "anthropic/claude-opus-5-5"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    mongodb_connection_string: SecretStr
    mongodb_database: str = "fde_activity_log"

    amp_connection: str = Field(default="private", description="AMP connection alias or UUID for every app")
    amp_retry_attempts: int = Field(default=4, description="Attempts per integration call, with exponential backoff")
    amp_retry_backoff_s: float = Field(default=2.0, description="First backoff delay; doubles on each retry")

    llm_model: str = Field(default=DEFAULT_LLM, description="Grouping, writing and the action point resolver")
    review_llm_model: str = Field(default=DEFAULT_REVIEW_LLM, description="The deep review agent (review requests)")

    event_retention_days: int = 90
    text_max_chars: int = 2000
    meeting_text_max_chars: int = 8000
    collector_timeout_s: int = 240
    grouping_max_chars: int = Field(default=160_000, description="Text budget for the day's grouping call")
    collection_settle_hours: int = Field(
        default=2, description="A source collected this long after the day ended is complete and reused"
    )
    force_recollect: bool = Field(default=False, description="Fetch every source again, ignoring stored data")
    review_ui_url: str | None = Field(
        default=None, description="Base URL of the review app; the review message links to the day there"
    )
    todoist_api_key: SecretStr | None = Field(
        default=None, description="The FDE's Todoist API token; action points are sent there when set"
    )

    reference_cache_days: int = 7
