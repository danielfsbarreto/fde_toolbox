from __future__ import annotations

from pymongo.database import Database

from fde_core.domain.learnings import Learning
from fde_core.repositories.indexes import LEARNINGS


class MongoLearningRepository:
    """The FDE's learnings. Runs only read the approved ones; the review app writes them."""

    def __init__(self, db: Database) -> None:
        self._collection = db[LEARNINGS]

    def approved(self, fde_id: str) -> list[Learning]:
        found = self._collection.find({"fde_id": fde_id, "status": "approved"}, {"_id": 0})
        return [Learning.model_validate(document) for document in found]
