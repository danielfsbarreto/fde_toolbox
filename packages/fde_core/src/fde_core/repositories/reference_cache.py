from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pymongo.database import Database

from fde_core.repositories.indexes import REFERENCE_CACHE


class MongoReferenceCacheRepository:
    def __init__(self, db: Database) -> None:
        self._collection = db[REFERENCE_CACHE]

    def get(self, fde_id: str, kind: str) -> tuple[Any, datetime] | None:
        document = self._collection.find_one({"fde_id": fde_id, "kind": kind})
        return (document["data"], document["fetched_at"]) if document else None

    def put(self, fde_id: str, kind: str, data: Any) -> None:
        self._collection.replace_one(
            {"fde_id": fde_id, "kind": kind},
            {"fde_id": fde_id, "kind": kind, "data": data, "fetched_at": datetime.now(timezone.utc)},
            upsert=True,
        )
