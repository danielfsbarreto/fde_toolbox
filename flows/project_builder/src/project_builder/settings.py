"""Environment-driven settings. Secrets come from env vars (AMP deployment env or Secrets Manager)."""

from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_BUILDER_LLM = "anthropic/claude-opus-5-5"  # specs, designs and code: the strongest model
DEFAULT_ROUTER_LLM = "anthropic/claude-haiku-5-5"  # one classification per turn: a fast one
DEFAULT_ENGINEER_LLM = "anthropic/claude-sonnet-5-5"  # writes the code: about twice the output speed of Opus


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    mongodb_connection_string: SecretStr
    mongodb_database: str = "fde_activity_log"
    fde_id: str = Field(default="daniel-barreto", description="Whose builds the local worker and terminal chat use")
    worker_concurrency: int = Field(default=4, description="Local worker: turns running at once (one per build)")
    builder_llm_model: str = Field(default=DEFAULT_BUILDER_LLM, description="The specialists and the conversation")
    router_llm_model: str = Field(default=DEFAULT_ROUTER_LLM, description="Picks the route of each turn")
    engineer_llm_model: str = Field(default=DEFAULT_ENGINEER_LLM, description="The engineer, who writes the code")
    e2b_api_key: SecretStr | None = Field(default=None, description="The sandboxes that run generated code")
    build_env_key: SecretStr | None = Field(
        default=None, description="Decrypts builds' env var values for their sandboxes (the review app's key)"
    )
    exa_api_key: SecretStr | None = Field(default=None, description="Web search for the architect (external APIs)")
    crewai_platform_integration_token: SecretStr | None = Field(
        default=None, description="Checks which AMP platform integrations a design's apps are connected to"
    )
