"""What the FDE adds to a day in review, kept per day and reapplied whenever the day is rebuilt.

- Manual entries: what the FDE did that no source recorded. They become manual events and "added by you"
  topics.
- Comments: context per account that is not a fact of a topic (a holiday, a meeting that did not happen).
- Feedback: what the FDE sent to the flow's review pause (agent requests, finalize), for the record.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from fde_core.domain.events import MANUAL_MATCH, ActivityEvent, Location, Person
from fde_core.domain.topics import FollowUp, Topic, Workstream, new_id
from fde_core.domain.window import DayWindow


class ManualEntry(BaseModel):
    """Something the FDE did that no source recorded (a call, a lunch, a whiteboard session)."""

    id: SkipJsonSchema[str] = Field(default_factory=new_id)

    account_key: str = Field(description='An owned account key, "internal" or "unassigned"')
    title: str = Field(description="Short title, like a topic title")
    what: str = Field(description="What happened, in the FDE's words, one to three sentences")
    workstream: Workstream = "other"
    time: str | None = Field(default=None, description="HH:MM local time, only if stated")
    duration_min: int | None = Field(default=None, description="Only if stated")
    people: list[str] = Field(default=[], description="People named")
    follow_ups: list[FollowUp] = Field(default=[], description="Next actions stated by the FDE")


class FeedbackRecord(BaseModel):
    at: datetime
    source: str = "flow"  # flow (terminal or AMP reply) or ui
    text: str


class DayOverrides(BaseModel):
    """Everything the FDE said about one day, kept and reapplied whenever the day is rebuilt."""

    fde_id: str
    date: date
    manual_entries: list[ManualEntry] = []
    comments: dict[str, str] = {}  # account key -> context the FDE gave in review (a holiday, a cancelled meeting)
    feedback: list[FeedbackRecord] = []

    def record(self, text: str, source: str = "flow") -> None:
        self.feedback.append(FeedbackRecord(at=datetime.now(timezone.utc), source=source, text=text))

    def manual_events(self, window: DayWindow, retention_days: int) -> list[ActivityEvent]:
        return [self._manual_event(entry, window, retention_days) for entry in self.manual_entries]

    def manual_topics(self, window: DayWindow, retention_days: int) -> list[Topic]:
        return [
            Topic(
                id=entry.id,
                title=entry.title,
                workstream=entry.workstream,
                what_happened=entry.what,
                my_involvement="led",
                follow_ups=entry.follow_ups,
                people=entry.people,
                evidence=[self._manual_event(entry, window, retention_days).id],
                confidence="high",
                inferred=False,
                account_key=entry.account_key,
                attribution="manual",
            )
            for entry in self.manual_entries
        ]

    def _manual_event(self, entry: ManualEntry, window: DayWindow, retention_days: int) -> ActivityEvent:
        stated = _parse_time(entry.time)
        local = datetime.combine(window.date, stated or time(0, 0), tzinfo=ZoneInfo(window.timezone))
        ts_utc = local.astimezone(timezone.utc)
        digest = hashlib.sha256(f"{entry.account_key}|{entry.title}|{entry.what}".encode()).hexdigest()[:12]
        return ActivityEvent(
            id=f"manual:{window.date.isoformat()}:{digest}",
            fde_id=self.fde_id,
            source="manual",
            date=window.date,
            ts_utc=ts_utc,
            ts_local=local,
            kind="note",
            involvement="authored",
            actor=Person(name="me", is_self=True),
            counterparts=[Person(name=name) for name in entry.people],
            location=Location(kind="workspace", id="manual", name="added by you"),
            title=entry.title,
            text=entry.what,
            account_key=entry.account_key,
            match_reason=MANUAL_MATCH,
            relevance="authored",
            meta={
                "action": "added by you",
                "time_stated": stated is not None,
                **({"duration_min": entry.duration_min} if entry.duration_min else {}),
            },
            expires_at=ts_utc + timedelta(days=retention_days),
        )


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value.strip())
    except ValueError:
        return None
