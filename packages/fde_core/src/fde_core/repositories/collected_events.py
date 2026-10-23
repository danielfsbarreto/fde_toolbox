from __future__ import annotations

from datetime import date

from pymongo import ReplaceOne
from pymongo.database import Database

from fde_core.domain.collection import SourceSnapshot
from fde_core.domain.events import ActivityEvent
from fde_core.repositories.bson import day_key, to_document
from fde_core.repositories.indexes import COLLECTED_EVENTS, SOURCE_SNAPSHOTS


class MongoCollectedEventRepository:
    """Raw per-source events (redacted, before matching) and per-source snapshots, so a run can reuse them."""

    def __init__(self, db: Database) -> None:
        self._events = db[COLLECTED_EVENTS]
        self._snapshots = db[SOURCE_SNAPSHOTS]

    def snapshots(self, fde_id: str, day: date) -> dict[str, SourceSnapshot]:
        cursor = self._snapshots.find({"fde_id": fde_id, "date": day_key(day)}, {"_id": 0})
        return {document["source"]: SourceSnapshot.model_validate(document) for document in cursor}

    def load(self, fde_id: str, day: date, source: str) -> list[ActivityEvent]:
        """Times come back in UTC: callers restore local time (they know the FDE's timezone)."""
        cursor = self._events.find({"fde_id": fde_id, "date": day_key(day), "source": source}, {"_id": 0})
        return [ActivityEvent.model_validate(document) for document in cursor]

    def save(self, snapshot: SourceSnapshot, events: list[ActivityEvent]) -> None:
        scope = {"fde_id": snapshot.fde_id, "date": day_key(snapshot.date), "source": snapshot.source}
        if any(event.fde_id != snapshot.fde_id or event.source != snapshot.source for event in events):
            raise ValueError("refusing to store events of another FDE or source")
        if events:
            self._events.bulk_write(
                [
                    ReplaceOne({"fde_id": snapshot.fde_id, "id": event.id}, to_document(event), upsert=True)
                    for event in events
                ],
                ordered=False,
            )
        self._events.delete_many({**scope, "id": {"$nin": [event.id for event in events]}})
        self._snapshots.replace_one(scope, to_document(snapshot), upsert=True)
