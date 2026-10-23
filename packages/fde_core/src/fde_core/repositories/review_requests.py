from __future__ import annotations

from datetime import date, datetime, timezone

from pymongo import DESCENDING
from pymongo.database import Database

from fde_core.domain.review_request import ReviewRequest
from fde_core.repositories.bson import day_key, to_document
from fde_core.repositories.indexes import REVIEW_REQUESTS


class MongoReviewRequestRepository:
    """The flow's review pauses. A new pause supersedes the day's older waiting ones."""

    def __init__(self, db: Database) -> None:
        self._collection = db[REVIEW_REQUESTS]

    def create(self, request: ReviewRequest) -> None:
        self._collection.update_many(
            {"fde_id": request.fde_id, "date": day_key(request.date), "status": "waiting"},
            {"$set": {"status": "superseded"}},
        )
        self._collection.insert_one(to_document(request))

    def next_round(self, fde_id: str, day: date) -> int:
        latest = self._collection.find_one(
            {"fde_id": fde_id, "date": day_key(day)}, {"round": 1}, sort=[("round", DESCENDING)]
        )
        return int(latest["round"]) + 1 if latest else 1

    def get(self, fde_id: str, request_id: str) -> ReviewRequest | None:
        document = self._collection.find_one({"fde_id": fde_id, "id": request_id}, {"_id": 0})
        return ReviewRequest.model_validate(document) if document else None

    def waiting_for_run(self, run_id: str) -> ReviewRequest | None:
        document = self._collection.find_one(
            {"run_id": run_id, "status": "waiting"}, {"_id": 0}, sort=[("round", DESCENDING)]
        )
        return ReviewRequest.model_validate(document) if document else None

    def mark_answered(self, request_id: str, source: str) -> bool:
        """True when this call answered it (it was still waiting)."""
        result = self._collection.update_one(
            {"id": request_id, "status": "waiting"},
            {"$set": {"status": "answered", "answered_at": datetime.now(timezone.utc), "answered_from": source}},
        )
        return result.modified_count == 1
