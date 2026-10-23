"""Merges collector output into one event list: dedupe by id, drop tool notifications whose source was collected."""

from __future__ import annotations

from collections import Counter

from fde_core.domain.events import ActivityEvent, CollectionResult, Source


class NormalizationService:
    def normalize(self, results: dict[Source, CollectionResult]) -> tuple[list[ActivityEvent], Counter[str]]:
        collected_ok = {source for source, result in results.items() if result.status == "ok"}
        dropped: Counter[str] = Counter()
        by_id: dict[str, ActivityEvent] = {}
        for result in results.values():
            for event in result.events:
                notification_of = event.meta.get("notification_of")
                if isinstance(notification_of, str) and notification_of in collected_ok:
                    dropped[f"notification:{notification_of}"] += 1
                    continue
                if event.id in by_id:
                    dropped["duplicate"] += 1
                    continue
                by_id[event.id] = event
        return sorted(by_id.values(), key=lambda event: event.ts_utc), dropped
