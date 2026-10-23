from __future__ import annotations

from datetime import date

from pymongo.database import Database

from fde_core.domain.review import DayOverrides
from fde_core.repositories.bson import day_key, to_document
from fde_core.repositories.indexes import DAY_OVERRIDES


class MongoDayOverridesRepository:
    """The FDE's review changes per day. Kept without expiry: they are part of the day's record."""

    def __init__(self, db: Database) -> None:
        self._collection = db[DAY_OVERRIDES]

    def get(self, fde_id: str, day: date) -> DayOverrides | None:
        document = self._collection.find_one({"fde_id": fde_id, "date": day_key(day)}, {"_id": 0})
        return DayOverrides.model_validate(document) if document else None

    def save(self, overrides: DayOverrides) -> None:
        key = {"fde_id": overrides.fde_id, "date": day_key(overrides.date)}
        self._collection.replace_one(key, to_document(overrides), upsert=True)
