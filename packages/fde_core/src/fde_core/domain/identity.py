"""Who the FDE is in each source, discovered from their AMP connections."""

from __future__ import annotations

from pydantic import BaseModel


class FdeIdentity(BaseModel):
    emails: list[str] = []
    slack_user_id: str | None = None
    teams_user_id: str | None = None
    github_login: str | None = None

    def is_self_email(self, email: str | None) -> bool:
        return bool(email) and email.strip().lower() in self.emails
