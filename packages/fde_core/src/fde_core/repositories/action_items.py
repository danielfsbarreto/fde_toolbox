from __future__ import annotations

from datetime import date

from pymongo.database import Database

from fde_core.domain.action_points import ActionItemRecord
from fde_core.repositories.bson import day_key, to_document
from fde_core.repositories.indexes import ACTION_ITEMS


class MongoActionItemRepository:
    """Tasks created in the tracker, one per action point key. Kept without expiry, like the review overrides."""

    def __init__(self, db: Database) -> None:
        self._collection = db[ACTION_ITEMS]

    def known_keys(self, fde_id: str, keys: list[str]) -> set[str]:
        found = self._collection.find({"fde_id": fde_id, "key": {"$in": keys}}, {"key": 1, "_id": 0})
        return {document["key"] for document in found}

    def list_day(self, fde_id: str, day: date) -> list[ActionItemRecord]:
        found = self._collection.find({"fde_id": fde_id, "date": day_key(day)}, {"_id": 0}).sort("created_at", 1)
        return [ActionItemRecord.model_validate(document) for document in found]

    def link_follow_ups(self, fde_id: str, key: str, follow_up_ids: list[str]) -> None:
        """A follow-up already sent can come back under a new id (an agent round rewrote its topic): keep both."""
        if follow_up_ids:
            self._collection.update_one(
                {"fde_id": fde_id, "key": key}, {"$addToSet": {"follow_up_ids": {"$each": follow_up_ids}}}
            )

    def record(self, item: ActionItemRecord) -> None:
        self._collection.replace_one({"fde_id": item.fde_id, "key": item.key}, to_document(item), upsert=True)
