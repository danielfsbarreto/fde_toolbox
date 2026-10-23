"""The DailyLog: the single source of truth for one FDE and one calendar day."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from fde_core.domain.events import ActivityEvent, CoverageStatus, Involvement, Source
from fde_core.domain.topics import FollowUp, Topic


class StaleLogError(RuntimeError):
    """The log changed since it was read (another edit, or the flow): read it again and retry."""


class ActivityLine(BaseModel):
    """One report line: an event, or a chat conversation (thread, DM, channel day) collapsed into one line.

    `event_id` names the line; `event_ids` are every event it stands for.
    """

    event_id: str
    event_ids: list[str] = []
    time: str
    source: Source
    kind: str
    involvement: Involvement
    summary: str
    url: str | None = None
    match_reason: str | None = None

    @classmethod
    def from_event(cls, event: ActivityEvent) -> ActivityLine:
        action = event.meta.get("action")
        label = action if isinstance(action, str) and action else event.kind
        subject = event.title or event.text.splitlines()[0] if (event.title or event.text) else ""
        return cls(
            event_id=event.id,
            event_ids=[event.id],
            time="--:--" if event.meta.get("time_stated") is False else event.ts_local.strftime("%H:%M"),
            source=event.source,
            kind=event.kind,
            involvement=event.involvement,
            summary=f"{label}: {subject[:140]}" if subject else label,
            url=event.url,
            match_reason=event.match_reason,
        )

    @classmethod
    def from_conversation(cls, events: list[ActivityEvent]) -> ActivityLine:
        """Chat messages of one thread or conversation, in time order."""
        first, last = events[0], events[-1]
        mine = sum(1 for event in events if event.actor.is_self)
        place = first.location.name or first.location.id
        if first.kind == "commit":
            place = f"{first.location.name}@{first.meta.get('branch', '')}"
        elif first.location.kind in ("issue", "repo"):
            place = first.title or place
        place = f"#{place}" if first.location.kind == "channel" else place
        where = f"{place} thread" if first.meta.get("in_thread") else place
        noun = {"comment": "comment", "commit": "commit"}.get(first.kind, "message")
        opener = next((event.text for event in events if event.text), "").splitlines()
        snippet = f": {opener[0][:120]}" if opener else ""
        involvement = "authored" if mine else max(events, key=lambda event: _RANK[event.involvement]).involvement
        span = first.ts_local.strftime("%H:%M")
        if last.ts_local.strftime("%H:%M") != span:
            span += f"-{last.ts_local.strftime('%H:%M')}"
        return cls(
            event_id=first.id,
            event_ids=[event.id for event in events],
            time=span,
            source=first.source,
            kind="conversation",
            involvement=involvement,
            summary=f"{where}, {len(events)} {noun}{'s' if len(events) > 1 else ''} ({mine} mine){snippet}",
            url=first.url,
            match_reason=first.match_reason,
        )


_RANK = {"observed": 0, "participated": 1, "mentioned": 2, "authored": 3}


class AccountDigest(BaseModel):
    account_key: str
    account_name: str
    headline: str
    topics: list[Topic] = []
    quiet: bool


class SourceCoverage(BaseModel):
    status: CoverageStatus
    event_count: int = 0
    error: str | None = None
    reused: bool = False
    collected_at: datetime | None = None


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    total_tokens: int = 0
    successful_requests: int = 0
    crew_totals: dict[str, int] = {}

    def add_crew(self, crew: str, total_tokens: int) -> None:
        self.crew_totals[crew] = self.crew_totals.get(crew, 0) + total_tokens


class RemovedFollowUp(BaseModel):
    """A follow-up removed in the review app, with its topic and place, kept so it can be restored."""

    topic_id: str
    index: int = 0
    follow_up: FollowUp


class DailyLog(BaseModel):
    fde_id: str
    date: date
    generated_at: datetime
    run_id: str
    schema_version: int = 1
    accounts: list[AccountDigest] = []
    internal: list[Topic] = []
    unassigned: list[Topic] = []
    comments: dict[str, str] = {}  # account key (or internal/unassigned) -> the FDE's comment from review
    removed: list[Topic] = []  # topics removed in review, kept so they can be restored
    removed_follow_ups: list[RemovedFollowUp] = []  # follow-ups removed in review (never synced), restorable
    activity: dict[str, list[ActivityLine]] = {}
    review_status: Literal["draft", "working", "final"] = "draft"  # working: the agent is applying requests
    version: int = 0  # bumped on every save; the review UI writes only on top of the version it read
    reviewed_at: datetime | None = None
    unapplied_feedback: list[str] = []  # notes from the agent on the last requests (what it could not do)
    coverage: dict[Source, SourceCoverage] = {}
    stats: dict[str, dict[str, int]] = {}
    warnings: list[str] = []
    usage: Usage = Usage()
    markdown: str = ""
