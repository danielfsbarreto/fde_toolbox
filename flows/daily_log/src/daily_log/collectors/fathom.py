"""Fathom: recorded meetings the FDE recorded or was invited to, with Fathom's summary.

The API key sees the whole team's shared recordings, so meetings are kept only when the FDE recorded them or
is a calendar invitee. Fathom filters on `created_at` (end of processing), so the listing is widened and each
recording is placed by its start time. Transcripts are not fetched: the summary is enough for the log.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from daily_log.collectors.common import CollectorErrors, iso_z, parse_ts, run_collector
from daily_log.ports.integrations import ActionError, ActionRunner
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, CollectionResult, Location, Person
from fde_core.domain.text import email_domain

APP = "fathom"
_MAX_PAGES = 30


class FathomCollector:
    source = "fathom"

    def __init__(self, actions: ActionRunner) -> None:
        self._actions = actions

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("fathom", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        meetings = [meeting for meeting in self._list(ctx, errors) if self._attended(ctx, meeting)]
        return [self._event(ctx, meeting, self._summary(meeting, errors)) for meeting in meetings]

    def _list(self, ctx: CollectionContext, errors: CollectorErrors) -> list[dict[str, Any]]:
        arguments: dict[str, Any] = {
            "created_after": iso_z(ctx.window.start_utc),
            "created_before": iso_z(ctx.window.end_utc + timedelta(days=1)),
        }
        meetings: list[dict[str, Any]] = []
        for page in range(_MAX_PAGES):
            try:
                output = self._actions.run(APP, "list_meetings", arguments)
            except ActionError as error:
                if page == 0:
                    raise
                errors.record(error)
                break
            meetings += [
                item
                for item in output.get("items", [])
                if (start := _start(item)) is not None and ctx.window.contains(start)
            ]
            cursor = output.get("next_cursor")
            if not cursor:
                break
            arguments["cursor"] = cursor
        return meetings

    @staticmethod
    def _attended(ctx: CollectionContext, meeting: dict[str, Any]) -> bool:
        emails = [(meeting.get("recorded_by") or {}).get("email")]
        emails += [invitee.get("email") for invitee in meeting.get("calendar_invitees") or []]
        return any(ctx.identity.is_self_email(email) for email in emails)

    def _summary(self, meeting: dict[str, Any], errors: CollectorErrors) -> str:
        try:
            output = self._actions.run(APP, "get_recording_summary", {"recording_id": meeting["recording_id"]})
        except ActionError as error:
            errors.record(error)
            return ""
        return ((output.get("summary") or {}).get("markdown_formatted") or "").strip()

    @staticmethod
    def _event(ctx: CollectionContext, meeting: dict[str, Any], summary: str) -> ActivityEvent:
        start = _start(meeting)
        assert start is not None
        recorder = meeting.get("recorded_by") or {}
        recorded_by_me = ctx.identity.is_self_email(recorder.get("email"))
        invitees = [
            Person(
                name=invitee.get("name"),
                email=(invitee.get("email") or "").lower() or None,
                domain=invitee.get("email_domain") or email_domain(invitee.get("email")),
                is_self=ctx.identity.is_self_email(invitee.get("email")),
            )
            for invitee in meeting.get("calendar_invitees") or []
        ]
        scheduled = parse_ts(meeting.get("scheduled_start_time"))
        end = parse_ts(meeting.get("recording_end_time")) or parse_ts(meeting.get("scheduled_end_time"))
        return ActivityEvent(
            id=f"fathom:{meeting['recording_id']}",
            fde_id=ctx.fde_id,
            source="fathom",
            date=ctx.window.date,
            ts_utc=start,
            ts_local=ctx.window.to_local(start),
            kind="meeting",
            involvement="authored" if recorded_by_me else "participated",
            actor=Person(
                name=recorder.get("name"),
                email=(recorder.get("email") or "").lower() or None,
                domain=recorder.get("email_domain") or email_domain(recorder.get("email")),
                is_self=recorded_by_me,
            ),
            counterparts=[person for person in invitees if not person.is_self],
            location=Location(kind="meeting", id=str(meeting["recording_id"]), name="Fathom"),
            thread_id=f"fathom:{meeting['recording_id']}",
            url=meeting.get("share_url") or meeting.get("url"),
            title=meeting.get("meeting_title") or meeting.get("title"),
            text=summary,
            meta={
                "action": "recorded" if recorded_by_me else "met",
                "scheduled_start": iso_z(scheduled) if scheduled else "",
                "duration_min": int((end - start).total_seconds() // 60) if end else 0,
                "language": meeting.get("transcript_language") or "",
            },
            expires_at=ctx.expires_at(start),
        )


def _start(meeting: dict[str, Any]):
    return parse_ts(meeting.get("recording_start_time")) or parse_ts(meeting.get("scheduled_start_time"))
