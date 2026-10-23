"""Keeps owned-account events and listed-internal events, plus anything the FDE authored, joined or was mentioned in.

Events that are internal only by default (no customer context) need the FDE's involvement to be kept.
"""

from __future__ import annotations

from collections import Counter

from fde_core.domain.events import EXPLICIT_INTERNAL, INTERNAL, UNASSIGNED, ActivityEvent, Relevance


class RelevanceService:
    def filter(self, events: list[ActivityEvent]) -> tuple[list[ActivityEvent], Counter[str]]:
        kept: list[ActivityEvent] = []
        dropped: Counter[str] = Counter()
        for event in events:
            relevance = self._relevance(event)
            if relevance is None:
                dropped[f"{event.source}:{event.involvement}"] += 1
            else:
                kept.append(event.model_copy(update={"relevance": relevance}))
        return kept, dropped

    @staticmethod
    def _relevance(event: ActivityEvent) -> Relevance | None:
        if event.account_key not in (INTERNAL, UNASSIGNED):
            return "owned_account"
        if event.account_key == INTERNAL and event.match_reason == EXPLICIT_INTERNAL:
            return "internal"
        if event.involvement in ("authored", "participated", "mentioned"):
            return event.involvement
        return None
