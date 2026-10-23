"""Google Calendar: meetings the FDE organized, accepted or tentatively accepted that day.

Skipped: declined or unanswered invitations, cancelled events, focus time, out of office, working location,
all-day entries, and events with no other attendees (personal blocks).
"""

from __future__ import annotations

from typing import Any

from daily_log.collectors.common import CollectorErrors, iso_z, parse_ts, run_collector
from daily_log.ports.integrations import ActionError, ActionRunner
from fde_core.domain.collection import CollectionContext
from fde_core.domain.events import ActivityEvent, CollectionResult, Location, Person
from fde_core.domain.text import email_domain

APP = "google_calendar"
_SKIPPED_TYPES = {"focusTime", "outOfOffice", "workingLocation", "birthday"}
_ATTENDING = {"accepted", "tentative"}


class CalendarCollector:
    source = "calendar"

    def __init__(self, actions: ActionRunner) -> None:
        self._actions = actions

    def collect(self, ctx: CollectionContext) -> CollectionResult:
        return run_collector("calendar", ctx, self._collect)

    def _collect(self, ctx: CollectionContext, errors: CollectorErrors) -> list[ActivityEvent]:
        events: list[ActivityEvent] = []
        for calendar_id in ctx.profile.sources.calendar.calendar_ids:
            for item in self._list(ctx, calendar_id, errors, required=not events):
                event = self._event(ctx, calendar_id, item)
                if event:
                    events.append(event)
        return events

    def _list(
        self, ctx: CollectionContext, calendar_id: str, errors: CollectorErrors, required: bool
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        page_token = None
        while True:
            arguments: dict[str, Any] = {
                "calendarId": calendar_id,
                "timeMin": iso_z(ctx.window.start_utc),
                "timeMax": iso_z(ctx.window.end_utc),
                "singleEvents": True,
                "orderBy": "startTime",
                "maxResults": 250,
            }
            if page_token:
                arguments["pageToken"] = page_token
            try:
                output = self._actions.run(APP, "view_events", arguments)
            except ActionError as error:
                if required and not items:
                    raise
                errors.record(error)
                break
            items += output.get("items", [])
            page_token = output.get("nextPageToken")
            if not page_token:
                return items
        return items

    def _event(self, ctx: CollectionContext, calendar_id: str, item: dict[str, Any]) -> ActivityEvent | None:
        start = parse_ts((item.get("start") or {}).get("dateTime"))
        end = parse_ts((item.get("end") or {}).get("dateTime"))
        if start is None or not ctx.window.contains(start):
            return None  # all-day entries have only `date`; events starting the day before are that day's
        if item.get("status") == "cancelled" or item.get("eventType") in _SKIPPED_TYPES:
            return None
        attendees = [attendee for attendee in item.get("attendees", []) if not attendee.get("resource")]
        me = next(
            (a for a in attendees if a.get("self") or ctx.identity.is_self_email(a.get("email"))),
            None,
        )
        organizer = item.get("organizer") or {}
        organized = bool(organizer.get("self")) or ctx.identity.is_self_email(organizer.get("email"))
        others = [a for a in attendees if a is not me and not ctx.identity.is_self_email(a.get("email"))]
        if not others:
            return None
        if not organized and (me is None or me.get("responseStatus") not in _ATTENDING):
            return None

        counterparts = [
            Person(
                name=a.get("displayName"),
                email=(a.get("email") or "").lower() or None,
                domain=email_domain(a.get("email")),
            )
            for a in others
        ]
        organizer_email = (organizer.get("email") or "").lower() or None
        description = item.get("description") or ""
        return ActivityEvent(
            id=f"calendar:{item['id']}",
            fde_id=ctx.fde_id,
            source="calendar",
            date=ctx.window.date,
            ts_utc=start,
            ts_local=ctx.window.to_local(start),
            kind="meeting",
            involvement="authored" if organized else "participated",
            actor=Person(
                name=organizer.get("displayName"),
                email=organizer_email,
                domain=email_domain(organizer_email),
                is_self=organized,
            ),
            counterparts=counterparts,
            location=Location(kind="calendar", id=calendar_id, name="Google Calendar"),
            thread_id=f"calendar:{item['id']}",
            url=item.get("htmlLink"),
            title=item.get("summary") or "(no title)",
            text=description,
            meta={
                "action": "met",
                "end_local": ctx.window.to_local(end).strftime("%H:%M") if end else "",
                "duration_min": int((end - start).total_seconds() // 60) if end else 0,
                "attendee_count": len(others) + 1,
                "response": (me or {}).get("responseStatus", "organizer"),
                "recurring": bool(item.get("recurringEventId")),
                "has_video": bool(item.get("hangoutLink") or item.get("conferenceData")),
                "ical_uid": item.get("iCalUID", ""),
            },
            expires_at=ctx.expires_at(start),
        )
