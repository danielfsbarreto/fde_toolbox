from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    mongodb_connection_string: SecretStr
    mongodb_database: str = "fde_activity_log"
    fde_id: str = Field(default="daniel-barreto", description="The FDE whose days this app reviews")

    hitl_webhook_secret: SecretStr = Field(description="Signing secret of the AMP Human in the Loop webhook")
    callback_encryption_key: SecretStr = Field(description="Fernet key; AMP callback URLs are stored encrypted")
    build_env_key: SecretStr | None = Field(
        default=None, description="Fernet key; builds' env var values are stored encrypted (the flow has it too)"
    )

    github_client_id: str
    github_client_secret: SecretStr
    review_ui_github_users: str = Field(description="Comma-separated GitHub logins allowed in")
    session_secret: SecretStr
    session_days: int = 7

    ngrok_domain: str | None = Field(default=None, description="Set only locally (bin/start): the app is dev")
    # The account report flow on AMP: the app starts it (POST /kickoff) when both are set; without them, reports are
    # saved and run locally with bin/flow account_report kickoff
    amp_report_url: str | None = Field(default=None, description="The account report deployment's URL")
    amp_report_token: SecretStr | None = Field(default=None, description="That deployment's bearer token")
    # The project builder flow on AMP: with both set, each message of a build is sent there as a chat turn; without
    # them, messages wait in MongoDB for a local worker (bin/flow project_builder worker)
    amp_builder_url: str | None = Field(default=None, description="The project builder deployment's URL")
    amp_builder_token: SecretStr | None = Field(default=None, description="That deployment's bearer token")

    web_dist: str | None = Field(default=None, description="The built frontend (ui/web/dist); found if unset")

    def allowed_users(self) -> set[str]:
        return {login.strip().lower() for login in self.review_ui_github_users.split(",") if login.strip()}
