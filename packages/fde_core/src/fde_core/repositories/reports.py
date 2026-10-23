from __future__ import annotations

from datetime import datetime, timezone

from pymongo.database import Database

from fde_core.domain.reports import Report, ReportContent
from fde_core.repositories.bson import to_document
from fde_core.repositories.indexes import REPORTS


class MongoReportRepository:
    """Account reports: the review app creates them (queued); the account_report flow fills them in."""

    def __init__(self, db: Database) -> None:
        self._collection = db[REPORTS]

    def get(self, fde_id: str, report_id: str) -> Report | None:
        document = self._collection.find_one({"fde_id": fde_id, "id": report_id}, {"_id": 0})
        return Report.model_validate(document) if document else None

    def find(self, report_id: str) -> Report | None:
        """By id alone: the flow's only input is the report id."""
        document = self._collection.find_one({"id": report_id}, {"_id": 0})
        return Report.model_validate(document) if document else None

    def start(self, fde_id: str, report_id: str) -> None:
        self._set(fde_id, report_id, status="running", started_at=datetime.now(timezone.utc), error=None)

    def finish(self, fde_id: str, report_id: str, content: ReportContent, tokens: int) -> None:
        self._set(
            fde_id,
            report_id,
            status="done",
            content=to_document(content),
            tokens=tokens,
            finished_at=datetime.now(timezone.utc),
        )

    def fail(self, fde_id: str, report_id: str, error: str) -> None:
        self._set(fde_id, report_id, status="failed", error=error[:500], finished_at=datetime.now(timezone.utc))

    def _set(self, fde_id: str, report_id: str, **fields: object) -> None:
        self._collection.update_one({"fde_id": fde_id, "id": report_id}, {"$set": fields})
