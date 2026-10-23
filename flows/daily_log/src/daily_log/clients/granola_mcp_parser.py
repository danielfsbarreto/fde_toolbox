"""Parser for the Granola MCP integration's text output.

The MCP tools return XML-like text meant for agents, not a versioned API:
    <meetings_data ...><meeting id=".." title=".." meeting_at=".." captured_by_me=".." ...>
      <known_participants>Name (Org) &lt;a@b.com&gt;, ...</known_participants><summary>...</summary>
    </meeting></meetings_data>
Parsing is strict: if the shape changes, GranolaFormatError says what is missing instead of guessing.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from email.utils import getaddresses

_MEETING = re.compile(r"<meeting\s+([^>]*)>(.*?)</meeting>", re.S)
_ATTR = re.compile(r'(\w+)="([^"]*)"')
_REQUIRED = ("id", "title", "meeting_at")


class GranolaFormatError(ValueError):
    """The Granola MCP output no longer looks like what this parser expects."""


@dataclass
class GranolaMeeting:
    id: str
    title: str
    meeting_at: str
    url: str | None
    captured_by_me: bool
    listed_as_participant: bool
    participants: list[tuple[str, str]] = field(default_factory=list)  # (name, email)
    summary: str = ""


def _section(body: str, tag: str) -> str:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", body, re.S)
    return html.unescape(match[1]).strip() if match else ""


def parse_meetings(text: str) -> list[GranolaMeeting]:
    if "<meetings_data" not in text:
        raise GranolaFormatError("no <meetings_data> element in Granola output")
    meetings = []
    for attributes, body in _MEETING.findall(text):
        attrs = {key: html.unescape(value) for key, value in _ATTR.findall(attributes)}
        missing = [name for name in _REQUIRED if not attrs.get(name)]
        if missing:
            raise GranolaFormatError(f"Granola meeting without {', '.join(missing)}")
        participants = [
            (name.strip(), email.strip().lower())
            for name, email in getaddresses([_section(body, "known_participants")])
            if email and "@" in email
        ]
        meetings.append(
            GranolaMeeting(
                id=attrs["id"],
                title=attrs["title"],
                meeting_at=attrs["meeting_at"],
                url=attrs.get("url"),
                captured_by_me=attrs.get("captured_by_me") == "true",
                listed_as_participant=attrs.get("listed_as_participant") == "true",
                participants=participants,
                summary=_section(body, "summary"),
            )
        )
    return meetings
