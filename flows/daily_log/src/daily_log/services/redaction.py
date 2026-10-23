"""Masks secrets and truncates text before events are stored or sent to an LLM."""

from __future__ import annotations

from fde_core.domain.events import ActivityEvent
from fde_core.domain.redaction import redact
from fde_core.domain.text import truncate


class RedactionService:
    def __init__(self, text_max_chars: int, meeting_text_max_chars: int) -> None:
        self._text_max_chars = text_max_chars
        self._meeting_text_max_chars = meeting_text_max_chars

    def apply(self, events: list[ActivityEvent]) -> tuple[list[ActivityEvent], int]:
        """Returns redacted copies and the number of secrets masked."""
        masked_total = 0
        redacted = []
        for event in events:
            text, masked_text = redact(event.text)
            title, masked_title = redact(event.title or "")
            masked_total += masked_text + masked_title
            limit = self._meeting_text_max_chars if event.kind == "meeting" else self._text_max_chars
            redacted.append(event.model_copy(update={"text": truncate(text, limit), "title": title or event.title}))
        return redacted, masked_total
