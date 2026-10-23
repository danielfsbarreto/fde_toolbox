from __future__ import annotations

from datetime import date
from typing import Any

from pymongo.database import Database

from fde_core.domain.daily_log import DailyLog, StaleLogError, Usage
from fde_core.repositories.bson import day_key, to_document
from fde_core.repositories.indexes import DAILY_LOGS


def _missing_ids(document: dict[str, Any]) -> bool:
    topics = [topic for digest in document.get("accounts", []) for topic in digest.get("topics", [])]
    topics += document.get("internal", []) + document.get("unassigned", []) + document.get("removed", [])
    return any("id" not in topic or any("id" not in item for item in topic.get("follow_ups", [])) for topic in topics)


class MongoDailyLogRepository:
    """One document per FDE and day. `version` grows on every save, so concurrent writers cannot overwrite."""

    def __init__(self, db: Database) -> None:
        self._collection = db[DAILY_LOGS]

    def save(self, log: DailyLog, expected_version: int | None = None) -> str:
        """Save the log. With `expected_version`, only if the stored log still has that version."""
        key = {"fde_id": log.fde_id, "date": day_key(log.date)}
        if expected_version is None:
            stored = self._collection.find_one(key, {"version": 1, "_id": 0}) or {}
            log.version = int(stored.get("version", 0)) + 1
            self._collection.replace_one(key, to_document(log), upsert=True)
        else:
            current = {"version": expected_version}
            if expected_version == 0:
                current = {"$or": [current, {"version": {"$exists": False}}]}
            log.version = expected_version + 1
            if self._collection.replace_one({**key, **current}, to_document(log)).matched_count == 0:
                log.version = expected_version
                raise StaleLogError(f"the log for {log.date} changed since version {expected_version}")
        return f"{DAILY_LOGS}/{log.fde_id}/{day_key(log.date)}"

    def get(self, fde_id: str, day: date) -> DailyLog | None:
        document = self._collection.find_one({"fde_id": fde_id, "date": day_key(day)}, {"_id": 0})
        if not document:
            return None
        log = DailyLog.model_validate(document)
        if _missing_ids(document):  # logs written before item ids existed: keep the ids they get now
            self.save(log)
        return log

    def set_usage(self, fde_id: str, day: date, usage: Usage) -> None:
        self._collection.update_one({"fde_id": fde_id, "date": day_key(day)}, {"$set": {"usage": usage.model_dump()}})
