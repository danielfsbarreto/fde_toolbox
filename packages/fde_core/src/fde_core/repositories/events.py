from __future__ import annotations

from datetime import date

from pymongo import ReplaceOne
from pymongo.database import Database

from fde_core.domain.events import ActivityEvent
from fde_core.repositories.bson import day_key, to_document
from fde_core.repositories.indexes import EVENTS


class MongoEventRepository:
    def __init__(self, db: Database) -> None:
        self._collection = db[EVENTS]

    def replace_day(self, fde_id: str, day: date, events: list[ActivityEvent]) -> int:
        if any(event.fde_id != fde_id for event in events):
            raise ValueError("refusing to store events of another FDE")
        if events:
            self._collection.bulk_write(
                [ReplaceOne({"fde_id": fde_id, "id": event.id}, to_document(event), upsert=True) for event in events],
                ordered=False,
            )
        stale = {"fde_id": fde_id, "date": day_key(day), "id": {"$nin": [event.id for event in events]}}
        return self._collection.delete_many(stale).deleted_count

    def list_day(self, fde_id: str, day: date) -> list[ActivityEvent]:
        cursor = self._collection.find({"fde_id": fde_id, "date": day_key(day)}, {"_id": 0})
        return [ActivityEvent.model_validate(document) for document in cursor.sort("ts_utc", 1)]
