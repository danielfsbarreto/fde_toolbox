"""One event per real meeting: recordings (Fathom, Granola) fold into the calendar event they belong to.

A recording belongs to a meeting when it starts within 15 minutes of it and shares a participant (email) or a
title. Time and attendees come from the calendar; each recorder's summary becomes a labelled section; every
source url is kept. Recordings with no calendar event (ad-hoc calls) are merged with each other the same way.
"""

from __future__ import annotations

from datetime import timedelta

from fde_core.domain.events import ActivityEvent, SourceRef
from fde_core.domain.text import fold

_WINDOW = timedelta(minutes=15)
_RECORDERS = ("fathom", "granola")
_LABELS = {"calendar": "Agenda", "fathom": "Fathom summary", "granola": "Granola summary"}


def _emails(event: ActivityEvent) -> set[str]:
    return {person.email for person in [event.actor, *event.counterparts] if person.email and not person.is_self}


def _same_meeting(anchor: ActivityEvent, recording: ActivityEvent) -> bool:
    if abs(anchor.ts_utc - recording.ts_utc) > _WINDOW:
        return False
    if _emails(anchor) & _emails(recording):
        return True
    a, b = fold(anchor.title or ""), fold(recording.title or "")
    return bool(a and b) and (a in b or b in a)


def _rank(event: ActivityEvent) -> tuple[bool, int]:
    return (event.involvement == "authored", len(event.text))


def _ref(event: ActivityEvent) -> SourceRef:
    return SourceRef(source=event.source, native_id=event.id.split(":", 1)[1], url=event.url)


class MeetingMergeService:
    def __init__(self, section_max_chars: int = 4000) -> None:
        self._section_max_chars = section_max_chars

    def merge(self, events: list[ActivityEvent]) -> tuple[list[ActivityEvent], int]:
        """Returns the events with recordings folded in, and how many recordings were merged."""
        anchors = [event for event in events if event.kind == "meeting" and event.source == "calendar"]
        recordings = sorted(
            (event for event in events if event.kind == "meeting" and event.source in _RECORDERS),
            key=lambda event: _RECORDERS.index(event.source),
        )
        others = [event for event in events if event not in anchors and event not in recordings]
        groups: list[list[ActivityEvent]] = [[anchor] for anchor in anchors]
        for recording in recordings:
            group = next((group for group in groups if _same_meeting(group[0], recording)), None)
            if group is None:
                groups.append([recording])
            else:
                group.append(recording)
        merged = [self._fold(group) for group in groups]
        return others + merged, len(recordings) + len(anchors) - len(merged)

    def _fold(self, group: list[ActivityEvent]) -> ActivityEvent:
        anchor = group[0]
        if len(group) == 1:
            return anchor.model_copy(update={"source_refs": [_ref(anchor)]})
        sections = [
            f"{_LABELS.get(event.source, event.source)}:\n{event.text[: self._section_max_chars]}"
            for event in self._one_per_source(group)
            if event.text.strip()
        ]
        people = {person.email or person.name: person for event in group for person in event.counterparts}
        recorded = sorted({event.source for event in group if event.source in _RECORDERS})
        return anchor.model_copy(
            update={
                "text": "\n\n".join(sections),
                "counterparts": [person for key, person in people.items() if key],
                "involvement": "authored"
                if any(event.involvement == "authored" for event in group)
                else anchor.involvement,
                "source_refs": [_ref(event) for event in group],
                "url": anchor.url or next((event.url for event in group if event.url), None),
                "meta": {**anchor.meta, "recorded_by": recorded, "merged_events": len(group)},
            }
        )

    @staticmethod
    def _one_per_source(group: list[ActivityEvent]) -> list[ActivityEvent]:
        """Several colleagues may record the same call: keep the FDE's own recording, else the longest summary."""
        best: dict[str, ActivityEvent] = {}
        for event in group:
            if event.source not in best or _rank(event) > _rank(best[event.source]):
                best[event.source] = event
        return [event for event in group if best.get(event.source) is event]
