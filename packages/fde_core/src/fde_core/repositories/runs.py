from __future__ import annotations

from datetime import datetime, timezone

from pymongo.database import Database

from fde_core.domain.action_points import SyncSummary
from fde_core.domain.daily_log import Usage
from fde_core.domain.run import DayResult, RunPlan
from fde_core.repositories.bson import to_document
from fde_core.repositories.indexes import RUNS


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MongoRunRepository:
    """One document per kickoff: inputs, per-day results (counts and coverage), usage, status, errors."""

    def __init__(self, db: Database) -> None:
        self._collection = db[RUNS]

    def start(self, plan: RunPlan, inputs: dict[str, str]) -> None:
        self._collection.replace_one(
            {"run_id": plan.run_id},
            {
                **to_document(plan),
                "inputs": inputs,
                "status": "running",
                "errors": [],
                "started_at": _now(),
            },
            upsert=True,
        )

    def record_result(self, run_id: str, fde_id: str, result: DayResult) -> None:
        self._collection.update_one({"run_id": run_id, "fde_id": fde_id}, {"$set": {"result": to_document(result)}})

    def finish(self, run_id: str, fde_id: str, status: str, usage: Usage) -> None:
        self._collection.update_one(
            {"run_id": run_id, "fde_id": fde_id},
            {"$set": {"status": status, "usage": usage.model_dump(), "finished_at": _now()}},
        )

    def record_action_points(self, run_id: str, fde_id: str, summary: SyncSummary) -> None:
        self._collection.update_one(
            {"run_id": run_id, "fde_id": fde_id}, {"$set": {"action_points": summary.model_dump()}}
        )

    def fail(self, run_id: str, fde_id: str, error: str) -> None:
        self._collection.update_one(
            {"run_id": run_id, "fde_id": fde_id},
            {"$set": {"status": "failed", "finished_at": _now()}, "$push": {"errors": error}},
        )
