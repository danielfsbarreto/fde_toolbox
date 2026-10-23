"""Activity events: the normalized unit every collector produces."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Source = Literal["gmail", "calendar", "slack", "teams", "github", "linear", "fathom", "granola", "manual"]
# Sources that have a collector. "manual" entries come from the FDE's review feedback.
SOURCES: tuple[Source, ...] = (
    "gmail",
    "calendar",
    "slack",
    "teams",
    "github",
    "linear",
    "fathom",
    "granola",
)
EventKind = Literal["message", "email", "meeting", "issue", "pr", "review", "commit", "comment", "note"]
Involvement = Literal["authored", "participated", "mentioned", "observed"]
Relevance = Literal["owned_account", "internal", "authored", "participated", "mentioned"]
CoverageStatus = Literal["ok", "partial", "failed", "disabled"]

INTERNAL = "internal"
UNASSIGNED = "unassigned"
EXPLICIT_INTERNAL = "internal:listed"
DEFAULT_INTERNAL = "internal:no_customer_context"
MANUAL_MATCH = "manual"


class Person(BaseModel):
    name: str | None = None
    email: str | None = None
    handle: str | None = None
    domain: str | None = None
    tenant_id: str | None = None
    is_self: bool = False


class Location(BaseModel):
    kind: Literal["channel", "dm", "group_dm", "chat", "repo", "mailbox", "calendar", "issue", "meeting", "workspace"]
    id: str
    name: str | None = None


class Attachment(BaseModel):
    name: str
    mime_type: str | None = None


class SourceRef(BaseModel):
    source: Source
    native_id: str
    url: str | None = None


class ActivityEvent(BaseModel):
    id: str = Field(description='"<source>:<native_id>"')
    fde_id: str
    source: Source
    date: date
    ts_utc: datetime
    ts_local: datetime
    kind: EventKind
    involvement: Involvement
    actor: Person
    counterparts: list[Person] = []
    location: Location
    thread_id: str | None = None
    url: str | None = None
    title: str | None = None
    text: str = ""
    language: str | None = None
    attachments: list[Attachment] = []
    account_key: str = UNASSIGNED
    match_reason: str | None = None
    relevance: Relevance | None = None
    source_refs: list[SourceRef] = []
    meta: dict[str, str | int | bool | list[str]] = {}
    expires_at: datetime


class CollectionResult(BaseModel):
    """What one collector returns for one day. Errors are short and never contain message content."""

    source: Source
    status: CoverageStatus
    events: list[ActivityEvent] = []
    error: str | None = None
