"""Granola: meetings the FDE captured or attended that day, with Granola's summary (through the AMP MCP integration)."""

from __future__ import annotations

from typing import Any

from daily_log.clients.granola_mcp_parser import GranolaMeeting, parse_meetings
from daily_log.collectors.common import CollectorErrors, parse_ts, run_collector
from daily_log.ports.integrations import ActionError, ActionRunner
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, CollectionResult, Location, Person
from fde_core.domain.text import email_domain

APP = "granola_mcp"
_BATCH = 10


def _text(output: dict[str, Any]) -> str:
    data = output.get("data", output)
    return data if isinstance(data, str) else ""


class GranolaCollector:
    source = "granola"

    def __init__(self, actions: ActionRunner) -> None:
        self._actions = actions

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("granola", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        day = ctx.window.date.isoformat()
        listing = self._actions.run(
            APP, "granola_mcp_list_meetings", {"time_range": "custom", "custom_start": day, "custom_end": day}
        )
        meetings = [
            meeting
            for meeting in parse_meetings(_text(listing))
            if (meeting.captured_by_me or meeting.listed_as_participant)
            and (start := parse_ts(meeting.meeting_at)) is not None
            and ctx.window.contains(start)
        ]
        summaries: dict[str, str] = {}
        for index in range(0, len(meetings), _BATCH):
            ids = [meeting.id for meeting in meetings[index : index + _BATCH]]
            try:
                output = self._actions.run(APP, "granola_mcp_get_meetings", {"meeting_ids": ids})
                summaries |= {meeting.id: meeting.summary for meeting in parse_meetings(_text(output))}
            except (ActionError, ValueError) as error:
                errors.record(error)
        return [self._event(ctx, meeting, summaries.get(meeting.id, "")) for meeting in meetings]

    @staticmethod
    def _event(ctx: CollectionContext, meeting: GranolaMeeting, summary: str) -> ActivityEvent:
        start = parse_ts(meeting.meeting_at)
        assert start is not None
        people = [
            Person(
                name=name or None, email=email, domain=email_domain(email), is_self=ctx.identity.is_self_email(email)
            )
            for name, email in meeting.participants
        ]
        return ActivityEvent(
            id=f"granola:{meeting.id}",
            fde_id=ctx.fde_id,
            source="granola",
            date=ctx.window.date,
            ts_utc=start,
            ts_local=ctx.window.to_local(start),
            kind="meeting",
            involvement="authored" if meeting.captured_by_me else "participated",
            actor=Person(name="me", is_self=True) if meeting.captured_by_me else Person(),
            counterparts=[person for person in people if not person.is_self],
            location=Location(kind="meeting", id=meeting.id, name="Granola"),
            thread_id=f"granola:{meeting.id}",
            url=meeting.url,
            title=meeting.title,
            text=summary,
            meta={"action": "recorded" if meeting.captured_by_me else "met"},
            expires_at=ctx.expires_at(start),
        )
