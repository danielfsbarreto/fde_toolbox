"""FDE profile: the configuration that makes the pipeline reusable across FDEs.

Profiles hold identifiers only. Source access goes through the FDE's own AMP connections,
and the FDE's per-app ids are discovered at run time (see domain/identity.py).
"""

from __future__ import annotations

import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from fde_core.domain.events import INTERNAL, UNASSIGNED, Source
from fde_core.domain.text import names_term

_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _domains(values: list[str]) -> list[str]:
    return [value.strip().lower().lstrip("@") for value in values if value.strip()]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceToggle(_Strict):
    enabled: bool = True


class CalendarSource(SourceToggle):
    calendar_ids: list[str] = ["primary"]


class GitHubSource(SourceToggle):
    orgs: list[str] = Field(default=[], description="Orgs scanned for account repos the FDE did not touch")


class Sources(_Strict):
    gmail: SourceToggle = SourceToggle()
    calendar: CalendarSource = CalendarSource()
    slack: SourceToggle = SourceToggle()
    teams: SourceToggle = SourceToggle()
    github: GitHubSource = GitHubSource()
    linear: SourceToggle = SourceToggle()
    fathom: SourceToggle = SourceToggle()
    granola: SourceToggle = SourceToggle()

    def is_enabled(self, source: Source) -> bool:
        return getattr(self, source).enabled


class TodoistTarget(_Strict):
    """Where an account's tasks go: "Project / Section", or "Project" for the project root."""

    project: str
    section: str | None = None

    @classmethod
    def parse(cls, text: str) -> TodoistTarget:
        project, _, section = text.partition(" / ")
        return cls(project=project.strip(), section=section.strip() or None)

    def __str__(self) -> str:
        return f"{self.project} / {self.section}" if self.section else self.project


def _todoist(value: object) -> list[TodoistTarget]:
    items = [value] if isinstance(value, str) else list(value or [])
    return [
        TodoistTarget.parse(item) if isinstance(item, str) else TodoistTarget.model_validate(item) for item in items
    ]


class OwnedAccount(_Strict):
    """Channels, chats and repos are discovered from the name, aliases and email domains.

    The explicit lists are optional overrides for places whose name never mentions the account.
    """

    key: str
    name: str
    description: str = Field(default="", description="What the engagement is; lets the agent suggest attributions")
    aliases: list[str] = []
    email_domains: list[str] = []
    slack_channels: list[str] = Field(default=[], description="Override: channel IDs")
    teams_channels: list[str] = Field(default=[], description='Override: "Team name/Channel name"')
    github_repos: list[str] = Field(default=[], description='Override: "owner/repo"')
    linear_customer_ids: list[str] = Field(default=[], description="Override: Linear customer ids")
    todoist: list[TodoistTarget] = Field(
        default=[], description='Where action points go: "Project / Section"; several = the agent picks, first default'
    )

    def terms(self) -> list[str]:
        return [self.key, self.name, *self.aliases]

    def names_place(self, place: str) -> str | None:
        """The first of this account's terms found in a place name (channel, chat, team, repo), if any."""
        return next((term for term in self.terms() if names_term(place, term)), None)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        if not _KEY.match(value) or value in (INTERNAL, UNASSIGNED):
            raise ValueError(
                f"account key {value!r} must be lowercase letters, digits, '-' or '_' "
                f"and must not be {INTERNAL!r} or {UNASSIGNED!r}"
            )
        return value

    @field_validator("email_domains")
    @classmethod
    def _email_domains(cls, values: list[str]) -> list[str]:
        return _domains(values)

    @field_validator("todoist", mode="before")
    @classmethod
    def _todoist_targets(cls, value: object) -> list[TodoistTarget]:
        return _todoist(value)

    @field_validator("github_repos")
    @classmethod
    def _repos(cls, values: list[str]) -> list[str]:
        for value in values:
            if value.count("/") != 1:
                raise ValueError(f"github repo {value!r} must look like 'owner/repo'")
        return [value.lower() for value in values]


class InternalScope(_Strict):
    email_domains: list[str] = []
    partner_email_domains: list[str] = Field(
        default=[], description="Partners who work with CrewAI on customer projects; treated as us, not outsiders"
    )
    slack_channels: list[str] = []
    keywords: list[str] = []
    todoist: list[TodoistTarget] = Field(default=[], description='Where internal action points go: "Project"')

    @field_validator("email_domains", "partner_email_domains")
    @classmethod
    def _email_domains(cls, values: list[str]) -> list[str]:
        return _domains(values)

    @field_validator("todoist", mode="before")
    @classmethod
    def _todoist_targets(cls, value: object) -> list[TodoistTarget]:
        return _todoist(value)

    def our_domains(self) -> list[str]:
        return [*self.email_domains, *self.partner_email_domains]


class FdeProfile(_Strict):
    schema_version: Literal[1] = 1
    fde_id: str
    name: str
    emails: list[str] = Field(min_length=1)
    timezone: str
    output_language: str = Field(default="en", pattern=r"^[a-z]{2}$")
    sources: Sources = Sources()
    owned_accounts: list[OwnedAccount] = []
    internal: InternalScope = InternalScope()

    @field_validator("fde_id")
    @classmethod
    def _fde_id(cls, value: str) -> str:
        if not _KEY.match(value):
            raise ValueError(f"fde_id {value!r} must be lowercase letters, digits, '-' or '_'")
        return value

    @field_validator("emails")
    @classmethod
    def _emails(cls, values: list[str]) -> list[str]:
        return [value.strip().lower() for value in values]

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError(f"unknown timezone {value!r}") from error
        return value

    @model_validator(mode="after")
    def _unique_accounts(self) -> FdeProfile:
        keys = [account.key for account in self.owned_accounts]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate owned account keys: {', '.join(duplicates)}")
        return self

    def account(self, key: str) -> OwnedAccount | None:
        return next((account for account in self.owned_accounts if account.key == key), None)
